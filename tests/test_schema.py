from sqlalchemy import text

# Literal transcription of SPEC.md §8.2 (table -> column set).
SPEC_8_2 = {
    "department": "id litellm_org_id name ad_group_dn budget budget_period pmo_pool reserve "
    "created_at updated_at",
    "department_model_allowance": "department_id model amount",
    "grp": "id litellm_team_id department_id name ad_group_dn kind base_budget "
    "default_member_cap overalloc_factor tpm_limit model_tpm_limits status",
    "group_model_pot": "group_id model amount",
    "employee": "upn display_name litellm_user_id group_id base_cap status key_token_hash "
    "max_parallel rpm_limit model_tpm_limits left_at",
    "employee_model_cap": "upn model amount",
    "grant": "id department_id group_id amount model project_ref purpose starts_at expires_at "
    "status created_by closed_at used returned",
    "grant_share": "id grant_id upn amount expires_at status used returned closed_reason",
    "service_key": "id owner_kind owner_id litellm_token_hash alias max_budget max_parallel "
    "rpm_limit models status",
    "policy": "key value",
    "role_binding": "ad_group_dn role scope_kind scope_id",
    "sync_run": "id started_at finished_at dry_run stats aborted_reason",
    "sync_action": "id run_id kind upn payload status error",
    "litellm_write": "id entity entity_id endpoint payload status attempts last_error "
    "idempotency_key created_at",
    "spend_snapshot": "scope_kind scope_id period model spend taken_at",
    "notification": "id upn kind scope_kind scope_id payload created_at read_at",
    "notification_pref": "upn kind muted",
    "audit_log": "id at actor_upn actor_role entity entity_id before after reason request_id",
}


async def test_tables_match_spec_8_2(engine):
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name <> 'alembic_version'"
                )
            )
        ).all()
    actual: dict[str, set[str]] = {}
    for table, column in rows:
        actual.setdefault(table, set()).add(column)
    assert actual == {t: set(c.split()) for t, c in SPEC_8_2.items()}
    assert len(SPEC_8_2) == 18
