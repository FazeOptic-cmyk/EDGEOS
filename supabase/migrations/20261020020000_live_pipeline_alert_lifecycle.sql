-- Durable alert lifecycle for live opportunity withdrawal/reactivation.
--
-- The trusted pipeline worker owns these fields. Browser roles retain only
-- owner-scoped SELECT plus acknowledgement updates (read/dismissed timestamps).

begin;

-- Research feeds may be denominated in cash tokens such as USDC/pUSD. Keep
-- the exact provider code; this widens storage only and does not imply a
-- currency conversion. Profile and paper-ledger fiat constraints stay intact.
alter table public.markets drop constraint if exists markets_currency_check;
alter table public.markets add constraint markets_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');
alter table public.fee_schedules drop constraint if exists fee_schedules_currency_check;
alter table public.fee_schedules add constraint fee_schedules_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');
alter table public.quotes drop constraint if exists quotes_currency_check;
alter table public.quotes add constraint quotes_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');
alter table public.opportunities drop constraint if exists opportunities_currency_check;
alter table public.opportunities add constraint opportunities_currency_check
  check (currency ~ '^[A-Za-z][A-Za-z0-9]{2,7}$');

alter table public.user_alert_events
  add column lifecycle_status text not null default 'active'
    check (lifecycle_status in ('active', 'withdrawn')),
  add column withdrawn_at timestamptz;

alter table public.user_alert_events
  add constraint user_alert_events_withdrawal_state_check
  check (
    (lifecycle_status = 'active' and withdrawn_at is null)
    or (lifecycle_status = 'withdrawn' and withdrawn_at is not null)
  );

create index user_alert_events_active_opportunity_idx
  on public.user_alert_events (opportunity_id, alert_id, triggered_at desc)
  where lifecycle_status = 'active' and opportunity_id is not null;

-- The original owner policies remain valid. Repeat grants explicitly so a
-- newly added column cannot broaden the browser mutation surface.
revoke all on table public.user_alert_events from anon, authenticated;
grant select on table public.user_alert_events to authenticated;
grant update (read_at, dismissed_at) on table public.user_alert_events to authenticated;
grant all privileges on table public.user_alert_events to service_role;

comment on column public.user_alert_events.lifecycle_status is
  'Worker-managed state: active while the referenced opportunity passes live gates, withdrawn otherwise.';
comment on column public.user_alert_events.withdrawn_at is
  'UTC time when live opportunity evidence ceased satisfying the alert; NULL while active.';

commit;
