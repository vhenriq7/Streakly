-- Registro remoto append-only. Nunca enviar uma chave service_role pelo aplicativo.
create table public.sync_operations (
    sequence bigint not null primary key,
    operation_id uuid not null,
    user_id uuid not null references auth.users (id) on delete cascade,
    entity text not null check (entity in (
        'subjects',
        'sessions',
        'session_events',
        'subject_timer_settings',
        'pomodoro_state',
        'daily_goal_defaults',
        'daily_goal_overrides'
    )),
    entity_id uuid not null,
    operation text not null check (operation in ('upsert', 'delete')),
    payload jsonb not null check (jsonb_typeof(payload) = 'object'),
    received_at timestamptz not null default now(),
    constraint sync_operations_idempotency unique (user_id, operation_id)
);

create sequence public.sync_operations_sequence_seq;
alter sequence public.sync_operations_sequence_seq
    owned by public.sync_operations.sequence;

create index sync_operations_user_cursor
    on public.sync_operations (user_id, sequence);

alter table public.sync_operations enable row level security;

revoke all on table public.sync_operations from public, anon, authenticated;
grant select, insert on table public.sync_operations to authenticated;
revoke all on sequence public.sync_operations_sequence_seq from public, anon, authenticated;
grant usage on sequence public.sync_operations_sequence_seq to authenticated;

create function public.assign_sync_operation_cursor()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    if auth.uid() is null or new.user_id is distinct from auth.uid() then
        raise exception 'sync operation user does not match authenticated user'
            using errcode = '42501';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(auth.uid()::text, 0)
    );
    new.sequence := pg_catalog.nextval(
        'public.sync_operations_sequence_seq'::pg_catalog.regclass
    );
    return new;
end;
$function$;

revoke all on function public.assign_sync_operation_cursor()
    from public, anon, authenticated;

create trigger sync_operations_assign_cursor
    before insert on public.sync_operations
    for each row
    execute function public.assign_sync_operation_cursor();

create policy sync_operations_read_own
    on public.sync_operations
    for select
    to authenticated
    using ((select auth.uid()) is not null and user_id = (select auth.uid()));

create policy sync_operations_insert_own
    on public.sync_operations
    for insert
    to authenticated
    with check ((select auth.uid()) is not null and user_id = (select auth.uid()));
