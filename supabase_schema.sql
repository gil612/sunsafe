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
