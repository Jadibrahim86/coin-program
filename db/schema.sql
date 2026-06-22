-- Coin program — databasschema
-- Postgres / Supabase. Kör i Supabase SQL Editor (eller psql).
-- Fas 0–1-tabellerna är aktiva nu; senare faser är definierade men skrivs inte än.
-- Se PLAN.md (§10, §11) för sammanhang.

-- =====================================================================
--  Fas 0–1 (aktiva nu)
-- =====================================================================

-- Coin-universum. Startlistan seedas av worker (cli.py seed-coins).
create table if not exists coins (
    id            bigint generated always as identity primary key,
    symbol        text not null unique,
    name          text not null,
    sector        text,                              -- L1 | L2 | DeFi | oracle | ...
    coingecko_id  text,                              -- för mcap/volym via CoinGecko
    renamed_from  text,                              -- t.ex. POL.renamed_from = 'MATIC'
    active        boolean not null default true,
    metadata      jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- Point-in-time medlemskap (§1). En rad per coin per datum: uppfyllde
-- coinen filterkriterierna då? Backtesten frågar historiskt, inte dagens lista.
create table if not exists universe_membership (
    id               bigint generated always as identity primary key,
    coin_id          bigint not null references coins(id) on delete cascade,
    as_of_date       date not null,
    qualified        boolean not null,
    market_cap_usd   numeric,
    volume_24h_usd   numeric,
    reason           jsonb,                          -- vilka kriterier som passerade/föll
    created_at       timestamptz not null default now(),
    unique (coin_id, as_of_date)
);
create index if not exists idx_universe_as_of on universe_membership (as_of_date);

-- OHLCV per coin/timeframe. gap_flag markerar bars som saknar föregångare.
create table if not exists ohlcv (
    coin_id    bigint not null references coins(id) on delete cascade,
    timeframe  text not null,                        -- '1h' | '4h' | '1d'
    ts         timestamptz not null,                 -- bar-öppningstid, UTC
    open       numeric not null,
    high       numeric not null,
    low        numeric not null,
    close      numeric not null,
    volume     numeric not null,
    gap_flag   boolean not null default false,       -- true om bars saknas före denna
    source     text,                                 -- börs-id (ccxt)
    primary key (coin_id, timeframe, ts)
);
create index if not exists idx_ohlcv_lookup on ohlcv (coin_id, timeframe, ts desc);

-- Derivatdata, aggregerad över venues (§3.2). Löpande snapshots framåt i tiden.
create table if not exists derivatives (
    coin_id          bigint not null references coins(id) on delete cascade,
    ts               timestamptz not null,
    open_interest    numeric,                        -- aggregerad OI (USD) över venues
    funding_rate     numeric,                        -- snittad funding över venues
    long_short_ratio numeric,
    oi_breakdown     jsonb,                          -- per-venue OI för transparens
    primary key (coin_id, ts)
);
create index if not exists idx_derivatives_lookup on derivatives (coin_id, ts desc);

-- =====================================================================
--  Senare faser (definierade, ej skrivna i Fas 0–1)
-- =====================================================================

-- Beräknade indikatorer/features per bar + regim-etikett (§4).
create table if not exists features (
    coin_id    bigint not null references coins(id) on delete cascade,
    timeframe  text not null,
    ts         timestamptz not null,
    values     jsonb not null,                       -- {rsi, atr, ema20, ...}
    regime     text,
    primary key (coin_id, timeframe, ts)
);

-- Genererade signaler (§5).
create table if not exists signals (
    id              bigint generated always as identity primary key,
    coin_id         bigint not null references coins(id),
    ts              timestamptz not null,
    direction       text not null,                   -- 'long' | 'short'
    composite_score numeric,
    entry           numeric,
    stop            numeric,
    tp              numeric[],
    rr              numeric,
    confidence      numeric,
    triggers        jsonb,                            -- vilka signaler som triggade
    regime          text,
    created_at      timestamptz not null default now()
);

-- Utfall per signal — grunden för det adaptiva lagret (§8 Steg A).
create table if not exists signal_outcomes (
    signal_id    bigint primary key references signals(id) on delete cascade,
    outcome      text,                                -- 'tp' | 'stop' | 'open'
    realized_rr  numeric,
    funding_paid numeric,
    closed_at    timestamptz
);

-- Adaptiva vikter per signal-familj och regim (rörs först i Fas 8).
create table if not exists weights (
    id            bigint generated always as identity primary key,
    signal_family text not null,
    regime        text not null,
    weight        numeric not null,
    updated_at    timestamptz not null default now(),
    unique (signal_family, regime)
);

-- Backtest-körningar med nyckeltal OCH baseline-nyckeltal (§7.2).
create table if not exists backtest_runs (
    id               bigint generated always as identity primary key,
    params           jsonb,
    period_start     date,
    period_end       date,
    metrics          jsonb,                           -- strategins nyckeltal
    baseline_metrics jsonb,                           -- buy-hold + random-entry baseline
    created_at       timestamptz not null default now()
);
