from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.auth import (
    template_available_users,
    template_current_roles,
    template_current_user,
    template_has_permission,
)
from app.i18n import template_lang_url, template_translate
from app.menu import template_sidebar_menu
from app.pagination import page_url
from app.security import csrf_token
from app.pv_ux import (
    contract_attention,
    contract_next_actions,
    contract_quality,
    partner_attention,
    partner_next_actions,
    partner_quality,
    product_attention,
    product_next_actions,
    product_quality,
    related_count_label,
    status_class,
)


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["_"] = template_translate
templates.env.globals["lang_url"] = template_lang_url
templates.env.globals["available_users"] = template_available_users
templates.env.globals["current_roles"] = template_current_roles
templates.env.globals["current_user"] = template_current_user
templates.env.globals["has_permission"] = template_has_permission
templates.env.globals["sidebar_menu"] = template_sidebar_menu
templates.env.globals["page_url"] = page_url
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["pv_partner_quality"] = partner_quality
templates.env.globals["pv_partner_attention"] = partner_attention
templates.env.globals["pv_partner_next_actions"] = partner_next_actions
templates.env.globals["pv_product_quality"] = product_quality
templates.env.globals["pv_product_attention"] = product_attention
templates.env.globals["pv_product_next_actions"] = product_next_actions
templates.env.globals["pv_contract_quality"] = contract_quality
templates.env.globals["pv_contract_attention"] = contract_attention
templates.env.globals["pv_contract_next_actions"] = contract_next_actions
templates.env.globals["pv_status_class"] = status_class
templates.env.globals["pv_related_count"] = related_count_label
