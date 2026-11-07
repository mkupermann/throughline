//! Team mode: projects only some accounts see (migration 018).
//!
//! A project is a value of `conversations.project_name`. A project without a
//! row in `restricted_projects` is visible to every account. A restricted
//! project is visible to the accounts in `project_access` and to admins.
//! PostgreSQL enforces it with row-level security for every query of an
//! account without the admin role. These functions only keep the two lists.

use anyhow::{bail, Context, Result};
use postgres::GenericClient;
use serde::Serialize;

/// A restricted project, its sessions and the accounts that see it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Restricted {
    pub project: String,
    pub sessions: i64,
    pub accounts: Vec<String>,
}

fn project_name(project: &str) -> Result<&str> {
    let project = project.trim();
    if project.is_empty() {
        bail!("name a project");
    }
    Ok(project)
}

fn account_id<C: GenericClient>(client: &mut C, account: &str) -> Result<i64> {
    client
        .query_opt(
            "SELECT id FROM access_users WHERE username = $1",
            &[&account.trim()],
        )
        .context("read accounts")?
        .map(|row| row.get(0))
        .with_context(|| format!("no account `{}`", account.trim()))
}

/// Restrict `project`. `false` when it already was.
pub fn restrict<C: GenericClient>(client: &mut C, project: &str) -> Result<bool> {
    let project = project_name(project)?;
    Ok(client.execute(
        "INSERT INTO restricted_projects (project_name) VALUES ($1) ON CONFLICT DO NOTHING",
        &[&project],
    )? == 1)
}

/// Lift the restriction of `project` and drop its access list. `false` when
/// it was not restricted.
pub fn unrestrict<C: GenericClient>(client: &mut C, project: &str) -> Result<bool> {
    let project = project_name(project)?;
    Ok(client.execute(
        "DELETE FROM restricted_projects WHERE project_name = $1",
        &[&project],
    )? == 1)
}

/// Let `account` see the restricted `project`. `false` when it already did.
pub fn allow<C: GenericClient>(client: &mut C, project: &str, account: &str) -> Result<bool> {
    let project = project_name(project)?;
    let user = account_id(client, account)?;
    let restricted: bool = client
        .query_one(
            "SELECT EXISTS (SELECT 1 FROM restricted_projects WHERE project_name = $1)",
            &[&project],
        )?
        .get(0);
    if !restricted {
        bail!("project `{project}` is not restricted: every account sees it already");
    }
    Ok(client.execute(
        "INSERT INTO project_access (project_name, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        &[&project, &user],
    )? == 1)
}

/// Take `project` away from `account`. `false` when it had no access.
pub fn revoke<C: GenericClient>(client: &mut C, project: &str, account: &str) -> Result<bool> {
    let project = project_name(project)?;
    let user = account_id(client, account)?;
    Ok(client.execute(
        "DELETE FROM project_access WHERE project_name = $1 AND user_id = $2",
        &[&project, &user],
    )? == 1)
}

/// Every restricted project with its session count and its accounts, by name.
pub fn list<C: GenericClient>(client: &mut C) -> Result<Vec<Restricted>> {
    let rows = client.query(
        "SELECT r.project_name,
                (SELECT count(*) FROM conversations c WHERE c.project_name = r.project_name),
                COALESCE((SELECT array_agg(u.username ORDER BY u.username)
                          FROM project_access a JOIN access_users u ON u.id = a.user_id
                          WHERE a.project_name = r.project_name), '{}')
         FROM restricted_projects r ORDER BY r.project_name",
        &[],
    )?;
    Ok(rows
        .iter()
        .map(|r| Restricted {
            project: r.get(0),
            sessions: r.get(1),
            accounts: r.get(2),
        })
        .collect())
}

/// Sessions of `project`, so a typo shows before it is restricted.
pub fn sessions<C: GenericClient>(client: &mut C, project: &str) -> Result<i64> {
    let project = project_name(project)?;
    Ok(client
        .query_one(
            "SELECT count(*) FROM conversations WHERE project_name = $1",
            &[&project],
        )?
        .get(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_db() -> Option<postgres::Client> {
        crate::config::DbConfig::from_env().connect().ok()
    }

    #[test]
    fn restrict_allow_revoke_and_list() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping restrict_allow_revoke_and_list: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin");
        tx.batch_execute(
            "CREATE TEMP TABLE access_users (LIKE public.access_users INCLUDING ALL) ON COMMIT DROP",
        )
        .expect("temp accounts");
        let user: i64 = tx
            .query_one(
                "INSERT INTO access_users (username, display_name, role, password_hash) \
                 VALUES ('ana', 'Ana', 'viewer', 'x') RETURNING id",
                &[],
            )
            .expect("account")
            .get(0);
        let project = format!("b10-{}", uuid::Uuid::new_v4().simple());
        assert!(restrict(&mut tx, &project).unwrap());
        assert!(!restrict(&mut tx, &project).unwrap(), "second time");
        assert!(allow(&mut tx, &project, "ana").unwrap());
        assert!(!allow(&mut tx, &project, "ana").unwrap(), "second time");
        assert!(allow(&mut tx, &project, "nobody").is_err());
        assert!(allow(&mut tx, "not-restricted-project-b10", "ana").is_err());
        let mine: Vec<_> = list(&mut tx)
            .unwrap()
            .into_iter()
            .filter(|r| r.project == project)
            .collect();
        assert_eq!(
            mine,
            [Restricted {
                project: project.clone(),
                sessions: 0,
                accounts: vec!["ana".into()],
            }]
        );
        let granted: i64 = tx
            .query_one(
                "SELECT user_id FROM project_access WHERE project_name = $1",
                &[&project],
            )
            .unwrap()
            .get(0);
        assert_eq!(granted, user);
        assert!(revoke(&mut tx, &project, "ana").unwrap());
        assert!(!revoke(&mut tx, &project, "ana").unwrap());
        assert!(unrestrict(&mut tx, &project).unwrap());
        assert!(!unrestrict(&mut tx, &project).unwrap());
        assert!(restrict(&mut tx, "  ").is_err());
    }
}
