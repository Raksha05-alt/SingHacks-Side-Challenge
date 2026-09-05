# Architecture overview

```
Client
  -> API Gateway
       -> checkout-service      (validates cart, reserves stock, creates order)
            -> inventory-service   (tracks per-SKU stock levels; reservation API)
       -> cart-service           (manages cart contents)
       -> pricing-service        (computes final price, applies promotions)
```

## Components

- **checkout-service**: stateless, calls `inventory-service` synchronously
  to reserve stock before confirming an order.
- **inventory-service**: owns per-SKU stock counters; exposes a
  reservation API with an internal retry policy for transient lock
  contention.
- **cart-service**: independent of checkout; manages cart contents prior
  to checkout.
- **pricing-service**: independent of checkout; computes price at
  checkout time from a cached price table.

`checkout-service` and `inventory-service` are the only components in
the direct path of a stock reservation.
