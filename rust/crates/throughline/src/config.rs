//! PostgreSQL connection settings from the standard `PG*` environment.

use std::env;

#[derive(Debug, Clone)]
pub struct DbConfig {
    pub host: String,
    pub port: u16,
    pub user: String,
    pub dbname: String,
    pub password: Option<String>,
}

impl Default for DbConfig {
    fn default() -> Self {
        Self::from_env()
    }
}

impl DbConfig {
    pub fn from_env() -> Self {
        let user = env::var("PGUSER").unwrap_or_else(|_| {
            env::var("USER")
                .or_else(|_| env::var("USERNAME"))
                .unwrap_or_else(|_| "postgres".into())
        });
        Self {
            host: env::var("PGHOST").unwrap_or_else(|_| "localhost".into()),
            port: env::var("PGPORT")
                .ok()
                .and_then(|p| p.parse().ok())
                .unwrap_or(5432),
            user,
            dbname: env::var("PGDATABASE").unwrap_or_else(|_| "throughline".into()),
            password: env::var("PGPASSWORD").ok().filter(|p| !p.is_empty()),
        }
    }

    pub fn connect(&self) -> anyhow::Result<postgres::Client> {
        use postgres::NoTls;
        let mut builder = postgres::Config::new();
        builder
            .host(&self.host)
            .port(self.port)
            .user(&self.user)
            .dbname(&self.dbname);
        if let Some(pw) = &self.password {
            builder.password(pw);
        }
        Ok(builder.connect(NoTls)?)
    }
}

/// Claude Code session directory. Overridable for tests and non-standard setups.
pub fn claude_projects_dir() -> std::path::PathBuf {
    if let Ok(dir) = env::var("THROUGHLINE_CLAUDE_PROJECTS_DIR") {
        return std::path::PathBuf::from(dir);
    }
    dirs::home_dir()
        .unwrap_or_default()
        .join(".claude")
        .join("projects")
}
