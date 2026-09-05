# Previous incidents

## INC-3105 (2026-04-02)

**Summary**: `pricing-service` served stale promotion prices for about
15 minutes after a rule update. Unrelated to checkout or reservations.

**Root cause**: cache refresh job ran later than expected.

**Resolution**: manual cache flush.

**MTTR**: 12 minutes.

No earlier incident record matches this exact combination of a checkout
failure alongside an inventory reservation retry - the closest prior
report (KI-214) never progressed to a confirmed incident, since the
reservation always eventually succeeded on its own.
