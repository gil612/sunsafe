-- SunSafe — Supabase schema (first slice: uv_readings + alerts_sent)
-- Run this once in the Supabase project's SQL editor.
-- See docs/superpowers/specs/2026-08-24-supabase-uv-logging-design.md for rationale.

create table if not exists uv_readings (
    id             bigint generated always as identity primary key,
    created_at     timestamptz not null default now(),
    query_city     text not null,
    resolved_city  text not null,
    country        text,
    lat            double precision not null,
    lon            double precision not null,
    uv_index       double precision not null,
    temperature_2m double precision,
    cloud_cover    integer
);

create table if not exists alerts_sent (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),
    uv_reading_id bigint references uv_readings(id),
    chat_id       text not null,
    message_text  text not null,
    parse_mode    text,
    status        text not null
);

alter table uv_readings enable row level security;
alter table alerts_sent enable row level security;

alter table alerts_sent add constraint alerts_sent_status_check
    check (status in ('sent', 'failed'));


-- SunSafe — Supabase schema (second slice: personal area — users,
-- exposure_log, magic_links)
-- See docs/2026-08-25-exposure-log-schema-design.md for rationale.

create table if not exists users (
    telegram_username text primary key,
    skin_type          smallint not null check (skin_type between 1 and 6),
    created_at          timestamptz not null default now()
);

create table if not exists exposure_log (
    id                bigint generated always as identity primary key,
    created_at        timestamptz not null default now(),
    telegram_username text not null references users(telegram_username),
    city              text not null,
    country           text,
    start_time        timestamptz not null,
    end_time          timestamptz,        -- NULL = session פתוח כרגע
    uv_index          double precision not null,
    spf               integer,            -- NULL = לא נעשה שימוש בקרם הגנה
    exposure_score    integer             -- NULL עד שה-session נסגר
);

create table if not exists magic_links (
    token              text primary key,
    telegram_username  text not null,
    expires_at         timestamptz not null,
    used               boolean not null default false,
    created_at         timestamptz not null default now()
);

alter table users enable row level security;
alter table exposure_log enable row level security;
alter table magic_links enable row level security;
-- שלוש הטבלאות האלה בלי אף policy בכוונה — גישה רק דרך service_role
-- (הבוט כותב/מעדכן, ה-Edge Function של ה-Magic Link קוראת). דפדפן עם
-- anon key לא יכול לגעת בהן ישירות בשום מצב.
