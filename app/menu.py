from copy import deepcopy
from types import SimpleNamespace

from jinja2 import pass_context


MENU_GROUPS = [
    {
        "key": "pv_plan",
        "label": "PV Plan",
        "icon": "clipboard-list",
        "page": "dashboard",
        "href": "/",
        "permissions": ["view"],
    },
    {
        "key": "partners_agreements",
        "label": "Partners and Agreements",
        "icon": "handshake",
        "children": [
            {
                "label": "Partners",
                "icon": "building-2",
                "page": "partners",
                "href": "/partners",
                "permissions": ["view", "manage_reference_data"],
            },
            {
                "label": "Contacts",
                "icon": "contact",
                "page": "contract_contacts",
                "href": "/contract-contacts",
                "permissions": ["view", "manage_reference_data"],
            },
            {
                "label": "PV agreements",
                "icon": "file-signature",
                "page": "contracts",
                "href": "/contracts",
                "permissions": ["view", "manage_reference_data"],
            },
        ],
    },
    {
        "key": "products",
        "label": "Products",
        "icon": "package",
        "children": [
            {
                "label": "Products",
                "icon": "package",
                "page": "products",
                "href": "/products",
                "permissions": ["view", "manage_reference_data"],
            },
            {
                "label": "Substances",
                "icon": "flask-conical",
                "page": "substances",
                "href": "/substances",
                "permissions": ["view", "manage_reference_data"],
            },
        ],
    },
    {
        "key": "safety_messages",
        "label": "Safety Messages",
        "icon": "inbox",
        "children": [
            {
                "label": "GPT analysis",
                "icon": "sparkles",
                "page": "incoming_requests",
                "href": "/incoming-requests",
                "permissions": ["view"],
            },
            {
                "label": "PV Intake",
                "icon": "inbox",
                "page": "safety_reports",
                "href": "/safety-reports",
                "permissions": ["view"],
            },
            {
                "label": "ICSR cases",
                "icon": "folder-open",
                "page": "cases",
                "href": "/cases",
                "permissions": ["view"],
            },
            {
                "label": "Partner reconciliation",
                "icon": "git-compare-arrows",
                "page": "partner_reconciliation",
                "href": "/partner-reconciliation",
                "permissions": ["view"],
            },
        ],
    },
    {
        "key": "literature_monitoring",
        "label": "Literature Monitoring",
        "icon": "search-check",
        "page": "literature_monitoring",
        "href": "/literature-monitoring",
        "permissions": ["view"],
    },
    {
        "key": "pv_modules",
        "label": "PV Modules",
        "icon": "layers-3",
        "children": [
            {
                "label": "PSMF",
                "icon": "library",
                "page": "psmf",
                "href": "/psmf",
                "permissions": ["view"],
            },
            {
                "label": "PSUR/PBRER Local",
                "icon": "calendar-check",
                "page": "psur",
                "href": "/psur",
                "permissions": ["view"],
            },
            {
                "label": "RMP",
                "icon": "shield-check",
                "page": "rmp",
                "href": "/rmp",
                "permissions": ["view"],
            },
            {
                "label": "SOP",
                "icon": "scroll-text",
                "page": "sops",
                "href": "/sops",
                "permissions": ["view"],
            },
        ],
    },
    {
        "key": "training",
        "label": "Training",
        "icon": "graduation-cap",
        "page": "training",
        "href": "/training",
        "permissions": ["view"],
    },
    {
        "key": "document_registry",
        "label": "Document Register",
        "icon": "files",
        "page": "documents",
        "href": "/documents",
        "permissions": ["view"],
    },
    {
        "key": "submissions_filings",
        "label": "Submissions and Filings",
        "icon": "send",
        "page": "submissions",
        "href": "/submissions",
        "permissions": ["view"],
    },
    {
        "key": "administration",
        "label": "Administration",
        "icon": "settings-2",
        "children": [
            {
                "label": "Users",
                "icon": "user-round",
                "page": "users_roles",
                "href": "/users-roles#users",
                "permissions": ["manage_users"],
            },
            {
                "label": "Roles",
                "icon": "shield-user",
                "page": "users_roles",
                "href": "/users-roles#roles",
                "permissions": ["manage_users"],
                "hash_only": True,
            },
            {
                "label": "Settings",
                "icon": "settings",
                "page": "settings",
                "href": "/settings",
                "permissions": ["manage_system_settings"],
            },
            {
                "label": "Dictionaries",
                "icon": "book-marked",
                "page": "settings",
                "href": "/settings#dictionaries",
                "permissions": ["manage_system_settings"],
                "hash_only": True,
            },
            {
                "label": "Audit Log",
                "icon": "history",
                "page": "audit_log",
                "href": "/audit-log",
                "permissions": ["audit_view"],
            },
        ],
    },
]


def _is_visible(item: dict, permissions: set[str]) -> bool:
    required_permissions = item.get("permissions") or []
    if not required_permissions:
        return True
    return any(permission in permissions for permission in required_permissions)


def _item_active(item: dict, active_page: str) -> bool:
    return item.get("page") == active_page and not item.get("hash_only")


def _active_pages(group: dict) -> set[str]:
    pages = set()
    if group.get("page"):
        pages.add(group["page"])
    for child in group.get("children", []):
        if child.get("page"):
            pages.add(child["page"])
    return pages


def build_menu_state(permission_codes, active_page: str = "dashboard") -> SimpleNamespace:
    permissions = set(permission_codes or [])
    groups = []

    for source_group in MENU_GROUPS:
        group = deepcopy(source_group)
        source_children = group.get("children", [])
        visible_children = [
            child for child in source_children if _is_visible(child, permissions)
        ]
        group["children"] = visible_children

        has_direct_page = bool(group.get("href") and group.get("page"))
        if source_children:
            if not visible_children and not has_direct_page:
                continue
        elif not _is_visible(group, permissions):
            continue

        if visible_children:
            if len(visible_children) == 1:
                only_child = visible_children[0]
                group["href"] = only_child["href"]
                group["page"] = only_child["page"]
                group["direct_child_label"] = only_child["label"]
                group["children"] = []
            else:
                group["href"] = None
                for child in group["children"]:
                    child["active"] = _item_active(child, active_page)

        group["active_pages"] = _active_pages(group)
        group["active"] = active_page in group["active_pages"]
        groups.append(group)

    current_group = next(
        (group["key"] for group in groups if group["active"]),
        groups[0]["key"] if groups else "",
    )
    for group in groups:
        group["active"] = group["key"] == current_group

    return SimpleNamespace(groups=groups, current_group=current_group)


@pass_context
def template_sidebar_menu(context, active_page: str = "dashboard") -> SimpleNamespace:
    request = context.get("request")
    permissions = getattr(getattr(request, "state", None), "permission_codes", set())
    return build_menu_state(permissions, active_page)
