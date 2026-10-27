"""Pricing domain logic: prompt construction, the Groq LLM call, and the offline fallback.

Orchestration (fetch signals -> rule price -> LLM -> fallback) lives in graph/; this module
only holds the pieces the graph nodes call.
"""
import os
import requests
from datetime import datetime

# Groq retires models regularly; override with a comma-separated GROQ_MODELS in .env
# instead of editing code. Check what your key can use at
# https://api.groq.com/openai/v1/models
DEFAULT_GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]


def get_groq_recommendation(prompt: str, api_key: str, json_mode: bool = False, reasoning_effort: str | None = None) -> str:
    """
    Calls Groq API to get a price recommendation with reasoning.
    Tries each model in GROQ_MODELS (or DEFAULT_GROQ_MODELS) in order.
    json_mode=True asks Groq to return a single JSON object (the prompt must mention JSON).
    reasoning_effort ("low" | "medium" | "high") applies to gpt-oss models only; "low" roughly halves latency.
    """
    if api_key and api_key.startswith("AQ.gsk_"):
        api_key = api_key[3:]  # Strip AQ. prefix if present

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    env_models = os.getenv("GROQ_MODELS")
    models_to_try = [m.strip() for m in env_models.split(",") if m.strip()] if env_models else DEFAULT_GROQ_MODELS

    errors = []
    for model in models_to_try:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.2
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_effort and model.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = reasoning_effort
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=60)
            if res.status_code == 200:
                res_data = res.json()
                return res_data["choices"][0]["message"]["content"]
            else:
                errors.append(f"{model}: status {res.status_code}: {res.text.strip()}")
        except Exception as e:
            errors.append(f"{model}: {e}")

    raise Exception("All Groq models failed:\n  " + "\n  ".join(errors))


def generate_mock_pricing_decision( 
    product_name: str,
    base_price: float,
    cost_price: float,
    inventory_stock: int,
    competitor_price: float,
    expiry_date,
    city: str,
    weather_signal: str,
    rule_based_price: float
) -> str:
    """
    Generates a high-quality, simulated AI pricing recommendation and reasoning.
    Used as a fallback when the Groq API key is missing or every Groq model fails.
    """
    # Determine days to expiry
    if isinstance(expiry_date, str):
        try:
            days_left = (datetime.strptime(expiry_date, '%Y-%m-%d').date() - datetime.now().date()).days
        except:
            days_left = 5
    elif hasattr(expiry_date, 'days'):
        days_left = expiry_date.days
    else:
        try:
            days_left = (expiry_date - datetime.now().date()).days
        except:
            days_left = 5

    # Determine recommended price
    # We want to be profitable (above cost_price) and competitive.
    # The rule-based price is rule_based_price (which has a floor of base_price * 0.7)
    suggested_price = max(rule_based_price, cost_price)
    
    # Adjust suggested price slightly for psychological pricing (e.g. $6.99 instead of $7.00),
    # but never let the rounding push us under the cost floor.
    if abs(suggested_price - round(suggested_price)) < 0.05 or suggested_price % 1.0 == 0:
        rounded = math_floor_pricing(suggested_price)
        suggested_price = rounded if rounded >= cost_price else math_ceil_pricing(suggested_price)

    profit_margin = ((suggested_price - cost_price) / suggested_price) * 100 if suggested_price > 0 else 0

    # 1. Stock Reasoning
    if inventory_stock > 30:
        stock_reason = f"We have **{inventory_stock} units** in stock, which is considered high stock (greater than 30). We apply a stock discount to increase sales volume."
    else:
        stock_reason = f"We have **{inventory_stock} units** in stock, which is low stock (<= 30). No volume discount is applied."

    # 2. Expiry Reasoning
    if days_left < 3:
        expiry_reason = f"The product is expiring in **{days_left} days** (on {expiry_date}), representing extreme urgency. A heavy clearance discount is applied to avoid a total write-off/loss."
    elif days_left < 7:
        expiry_reason = f"The product is expiring in **{days_left} days** (on {expiry_date}), which is near-expiry (less than 7 days). A clearance discount is applied to accelerate inventory velocity."
    else:
        expiry_reason = f"The product has **{days_left} days** before expiry (on {expiry_date}), so there is no expiry pressure."

    # 3. Competitor Reasoning
    if suggested_price < competitor_price:
        diff_pct = ((competitor_price - suggested_price) / competitor_price) * 100
        comp_reason = f"Our suggested price of **${suggested_price:.2f}** is **{diff_pct:.1f}% cheaper** than the competitor's price of **${competitor_price:.2f}**, giving us a strong competitive advantage."
    elif abs(suggested_price - competitor_price) < 0.01:
        comp_reason = f"Our suggested price of **${suggested_price:.2f}** matches the competitor's price of **${competitor_price:.2f}**, staying aligned with market rates."
    else:
        diff_pct = ((suggested_price - competitor_price) / competitor_price) * 100
        comp_reason = f"Our suggested price of **${suggested_price:.2f}** is **{diff_pct:.1f}% higher** than the competitor's price of **${competitor_price:.2f}**. This premium is maintained to protect our cost floor of **${cost_price:.2f}**."

    # 4. Weather Reasoning
    weather_reason = f"Weather in {city} is reported as: \"{weather_signal}\". "
    if "C" in weather_signal:
        try:
            parts = weather_signal.split(":")
            if len(parts) > 1:
                temp_str = parts[1].strip().split("C")[0].strip()
                temp = float(temp_str)
                if temp > 28:
                    weather_reason += f"The high temperature of {temp}°C increases spoilage rates for fresh/perishable goods like {product_name}, making immediate inventory clearance crucial."
                elif temp < 15:
                    weather_reason += f"The cool weather of {temp}°C helps preserve perishable goods, giving us slightly more buffer, but might shift demand patterns."
                else:
                    weather_reason += f"Moderate temperatures of {temp}°C indicate normal storage conditions and steady demand."
        except:
            weather_reason += f"We monitor these local conditions to adjust pricing based on spoilage risk and foot traffic."
    else:
        weather_reason += f"We monitor these local conditions to adjust pricing based on spoilage risk and foot traffic."

    reasoning = f"""### Final Suggested Price: ${suggested_price:.2f}
### Profit Margin: {profit_margin:.1f}%

### Strategy & Reasoning:
1. **Cost & Profitability Analysis**: 
   - The cost floor is **${cost_price:.2f}**. The proposed price of **${suggested_price:.2f}** yields a profit margin of **{profit_margin:.1f}%**.

2. **Inventory & Expiry Pressures**:
   - {stock_reason}
   - {expiry_reason}

3. **Competitor Comparison**:
   - {comp_reason}

4. **Weather Influence**:
   - {weather_reason}
"""
    return reasoning

def math_floor_pricing(price: float) -> float:
    # Small helper to make prices look nice (e.g. ending in .99 or .95)
    base = int(price)
    if price - base > 0.5:
        return base + 0.99
    elif price - base > 0.0:
        return base + 0.49
    else:
        return price - 0.01

def math_ceil_pricing(price: float) -> float:
    # Rounds up to the next .49/.99 ending, for when rounding down would break the cost floor
    base = int(price)
    if price - base <= 0.49:
        return base + 0.49
    return base + 0.99

def build_pricing_prompt(
    product_name: str,
    base_price: float,
    cost_price: float,
    inventory_stock: int,
    competitor_price: float,
    expiry_date: str,
    weather_signal: str,
    rule_based_price: float
) -> str:
    """Builds the LLM prompt from the gathered signals. expiry_date is an ISO date string."""
    return f"""
You are an expert AI Pricing Strategist for a retail store.
Your goal is to propose an optimal adaptive final selling price for a product that maximizes profit while remaining competitive and clearing stock efficiently when needed.

We have gathered the following signals for the product:
- **Product Name**: {product_name}
- **Original Base/Retail Price**: ${base_price:.2f}
- **Cost Price (Floor limit to ensure profitability)**: ${cost_price:.2f}
- **Current Inventory Stock**: {inventory_stock} units
- **Expiry Date**: {expiry_date}
- **Competitor Price**: ${competitor_price:.2f}
- **Local Weather Signal**: {weather_signal}
- **Rule-based Baseline Price**: ${rule_based_price:.2f}

Guidelines:
1. **Profitability**: The suggested price should not go below the Cost Price of ${cost_price:.2f} unless absolutely necessary to avoid total loss (e.g., severe expiry pressure).
2. **Competitiveness**: If the competitor price is lower, consider matching or staying close to it to prevent losing customers, but adjust based on stock level and expiry urgency.
3. **Inventory & Expiry**: If stock is high or expiry is very close, apply an appropriate discount to accelerate sales.
4. **Weather Impact**: Consider how weather affects demand for {product_name} (e.g., hot weather might increase demand for ice cream/cold beverages or increase spoilage rates of fresh fruits/vegetables).

Please provide your response in the following format:
### Final Suggested Price: $[amount]
### Profit Margin: [percentage]%
### Strategy & Reasoning:
[Provide a step-by-step reasoning for the final suggested price, highlighting the influence of inventory, expiry, competitor price, and weather signals.]
"""
