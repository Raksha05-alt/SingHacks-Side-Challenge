# Runbooks

## RB-031: Checkout Reservation Retry Pattern

**Symptoms**: `checkout-service` logs a `500 INTERNAL_ERROR` while
`inventory-service` shows a reservation retry around the same timestamp.

**Diagnostic steps**:
1. Check `inventory-service` lock-contention metrics for the affected
   SKU.
2. Check whether the checkout timeout (3000ms) is shorter than the
   observed retry latency.

**Remediation**: increase the checkout-to-inventory timeout, or reduce
inventory-service's retry backoff further so a retry completes within
the existing timeout window.

**Typical MTTR: 25 minutes.**

This pattern has only been seen in a handful of isolated reports, and a
direct causal link between the retry and the checkout failure has not
been established - it is equally possible the two are coincidental given
how rarely either occurs on its own.

## RB-002: Elevated Notification Queue Depth

**Symptoms**: `notification-service` logging elevated queue depth
warnings.

**Diagnostic steps**: check consumer count and downstream email provider
latency.

**Remediation**: scale notification-service consumers.

**Typical MTTR: 15 minutes.**
