from datetime import datetime, timedelta

def get_vendor_price(base_price: float, inventory_stock: int, competitor_price: float, expiry_date) -> float:
    final_price = base_price
    
    if (expiry_date - datetime.now().date()).days < 7:
        discount = 0.4
        final_price -= discount * final_price
        
    if inventory_stock > 30:
        discount = 0.15
        final_price -= discount * final_price
    if competitor_price < final_price and competitor_price > final_price * 0.85:
        final_price = competitor_price
    
    min_price = base_price * 0.7
    final_price = max(final_price, min_price)
    return final_price

if __name__ == "__main__":
    today = datetime.now().date()
    near_expiry = today + timedelta(days=5)
    far_expiry = today + timedelta(days=15)
    
    test_cases = [
        {
            "name": "near-expiry + low stock",
            "base_price": 100.0,
            "inventory_stock": 10,
            "competitor_price": 95.0,
            "expiry_date": near_expiry
        },
        {
            "name": "near-expiry + high stock",
            "base_price": 100.0,
            "inventory_stock": 50,
            "competitor_price": 95.0,
            "expiry_date": near_expiry
        },
        {
            "name": "competitor much cheaper",
            "base_price": 100.0,
            "inventory_stock": 10,
            "competitor_price": 50.0,
            "expiry_date": far_expiry
        },
        {
            "name": "competitor only slightly cheaper",
            "base_price": 100.0,
            "inventory_stock": 10,
            "competitor_price": 90.0,
            "expiry_date": far_expiry
        },
        {
            "name": "normal/no-trigger case",
            "base_price": 100.0,
            "inventory_stock": 10,
            "competitor_price": 105.0,
            "expiry_date": far_expiry
        }
    ]
    
    print("Running Test Cases through get_vendor_price:")
    print("=" * 60)
    for i, tc in enumerate(test_cases, 1):
        result = get_vendor_price(
            base_price=tc["base_price"],
            inventory_stock=tc["inventory_stock"],
            competitor_price=tc["competitor_price"],
            expiry_date=tc["expiry_date"]
        )
        print(f"Test {i}: {tc['name']}")
        print(f"  Inputs: Base Price={tc['base_price']}, Stock={tc['inventory_stock']}, Competitor={tc['competitor_price']}, Expiry={tc['expiry_date']}")
        print(f"  Output Price: {result:.2f}")
        print("-" * 60)