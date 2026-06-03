from __future__ import annotations

# Permissions that allow broad tenant-level write/control
CRITICAL_PERMISSIONS = {
    "Application.ReadWrite.All",
    "Directory.ReadWrite.All",
    "RoleManagement.ReadWrite.Directory",
    "AppRoleAssignment.ReadWrite.All",
}

# Permissions with significant but non-tenant-wide write scope
HIGH_PERMISSIONS = {
    "Group.ReadWrite.All",
    "User.ReadWrite.All",
    "Mail.ReadWrite",
    "Files.ReadWrite.All",
    "Sites.FullControl.All",
    "Exchange.ManageAsApp",
}


def calculate_risk_score(sp: dict) -> tuple[int, str]:
    """Return (score 0-100, level OK/MEDIUM/HIGH/CRITICAL) for a Service Principal.

    Scoring components:
      - Inactivity          0-30
      - Age (never logged)  0-25
      - Dangerous perms     0-40
      - Interaction bonus   0-25  (dangerous perm AND inactive > 90d)
      - Expired creds       0-30
      - Permission count    0-10
    """
    score = 0

    days_since_sign_in: int | None = sp.get("days_since_sign_in")

    # Inactivity (0-30)
    if days_since_sign_in is None:
        score += 30
    elif days_since_sign_in > 365:
        score += 25
    elif days_since_sign_in > 180:
        score += 15
    elif days_since_sign_in > 90:
        score += 5

    # Age bonus — only when SP has never signed in (0-25)
    sp_age_days: int = sp.get("sp_age_days", 0)
    if days_since_sign_in is None:
        if sp_age_days > 365:
            score += 25
        elif sp_age_days > 180:
            score += 10

    # Permission risk (0-40)
    permissions: list[str] = sp.get("permissions", [])
    has_critical = any(p in CRITICAL_PERMISSIONS for p in permissions)
    high_count = sum(1 for p in permissions if p in HIGH_PERMISSIONS)

    if has_critical:
        score += 40
    elif high_count > 0:
        score += min(high_count * 20, 40)

    # Interaction bonus: elevated perm combined with inactivity (0-25)
    # A dangerous permission on a dormant SP is significantly worse than either alone.
    is_inactive = days_since_sign_in is None or days_since_sign_in > 90
    if (has_critical or high_count > 0) and is_inactive:
        score += 25

    # Expired credentials (0-30)
    if sp.get("has_expired_creds", False):
        score += 30

    # Permission count (0-10)
    permission_count: int = sp.get("permission_count", len(permissions))
    if permission_count > 50:
        score += 10
    elif permission_count > 20:
        score += 5

    score = min(score, 100)

    if score <= 25:
        level = "OK"
    elif score <= 50:
        level = "MEDIUM"
    elif score <= 75:
        level = "HIGH"
    else:
        level = "CRITICAL"

    return score, level
