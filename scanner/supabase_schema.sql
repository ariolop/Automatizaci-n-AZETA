-- ============================================================================
--  Tabla de historial + CACHÉ de escaneos para el Escáner AZETA + Liderpapel.
--  Cómo usar: en Supabase -> SQL Editor -> pega esto -> Run.
--
--  Guarda por cada escaneo: precio neto, coste real y PVP de cada proveedor,
--  más un snapshot completo (columna 'payload') que se usa como caché: al
--  reescanear el mismo EAN se muestra al instante sin volver a consultar.
--
--  La app escribe con la key 'service_role', que ignora RLS, así que no hace
--  falta configurar políticas. (Si prefieres la key 'anon', descomenta el
--  bloque RLS del final.)
-- ============================================================================

create table if not exists public.escaneos (
  id                 bigint generated always as identity primary key,
  creado_at          timestamptz not null default now(),
  ean                text not null,
  encontrado         boolean,
  -- AZETA
  azeta_encontrado   boolean,
  azeta_nombre       text,
  azeta_precio_neto  numeric,
  azeta_coste        numeric,
  azeta_pvp          numeric,
  azeta_disponible   boolean,
  -- Liderpapel / CS Papelería
  cs_encontrado      boolean,
  cs_nombre          text,
  cs_precio_neto     numeric,
  cs_coste           numeric,
  cs_disponible      boolean,
  -- Comparación y snapshot completo (caché)
  mas_barato         text,
  payload            jsonb
);

create index if not exists escaneos_creado_idx on public.escaneos (creado_at desc);
create index if not exists escaneos_ean_idx    on public.escaneos (ean);
-- Índice para la caché (último encontrado por EAN)
create index if not exists escaneos_cache_idx  on public.escaneos (ean, encontrado, creado_at desc);

-- ---------------------------------------------------------------------------
-- MIGRACIÓN: si YA habías creado la tabla antes (versión sin precios/caché),
-- ejecuta solo estas líneas para añadir las columnas nuevas (son idempotentes):
-- ---------------------------------------------------------------------------
-- alter table public.escaneos add column if not exists azeta_precio_neto numeric;
-- alter table public.escaneos add column if not exists azeta_pvp         numeric;
-- alter table public.escaneos add column if not exists cs_precio_neto     numeric;
-- alter table public.escaneos add column if not exists payload            jsonb;

-- ---------------------------------------------------------------------------
-- OPCIONAL: solo si vas a usar la key 'anon' en lugar de 'service_role'.
-- ---------------------------------------------------------------------------
-- alter table public.escaneos enable row level security;
-- create policy "insertar escaneos" on public.escaneos for insert with check (true);
-- create policy "leer escaneos"     on public.escaneos for select using (true);
