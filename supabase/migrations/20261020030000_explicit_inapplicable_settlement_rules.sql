-- Explicit source evidence may establish that an exceptional settlement rule
-- does not apply. This is distinct from a missing or unknown treatment.
alter table public.market_rules
  drop constraint market_rules_void_treatment_check,
  add constraint market_rules_void_treatment_check
    check (void_treatment is null or void_treatment in
      ('refund', 'loss', 'regrade', 'not_applicable', 'unknown')),
  drop constraint market_rules_cancellation_treatment_check,
  add constraint market_rules_cancellation_treatment_check
    check (cancellation_treatment is null or cancellation_treatment in
      ('refund', 'action', 'reschedule', 'not_applicable', 'unknown'));
