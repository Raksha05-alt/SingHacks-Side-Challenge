# API specification (excerpt)

## `POST /api/checkout`

Owned by: `checkout-service`, delegates to `inventory-service`.

| Field | Type | Notes |
|---|---|---|
| `cart_id` | string | required |
| `payment_token` | string | required |

**Timeout**: `checkout-service` waits up to 3000ms for `inventory-service`
to confirm a reservation before failing the request with `500
INTERNAL_ERROR`. Retries are handled internally by `inventory-service`,
not by the caller.

**Response codes**: `200 OK`, `409 OUT_OF_STOCK`, `500 INTERNAL_ERROR`.

## `POST /api/cart/items`

Owned by `cart-service`. Independent of the checkout path; does not call
`inventory-service`.
