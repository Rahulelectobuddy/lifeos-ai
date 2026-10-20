-- Run as your existing PostgreSQL administrator; passwords are supplied separately.
-- These example role names may be changed to match your infrastructure policy.
CREATE ROLE lifeos_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE lifeos_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE SCHEMA lifeos AUTHORIZATION lifeos_migrator;
GRANT USAGE ON SCHEMA lifeos TO lifeos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE lifeos_migrator IN SCHEMA lifeos
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lifeos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE lifeos_migrator IN SCHEMA lifeos
  GRANT USAGE, SELECT ON SEQUENCES TO lifeos_app;
-- Set passwords with psql's \password command, never paste them into versioned SQL.
-- Connect privilege must be granted on YOUR existing database when restricted.
-- Grant only lifeos schema data privileges after migrations:
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lifeos TO lifeos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lifeos TO lifeos_app;
-- lifeos_app is never granted CREATE on lifeos; runtime never invokes DDL.
