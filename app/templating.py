from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.auth import (
    template_available_users,
    template_current_roles,
    template_current_user,
    template_has_permission,
)
from app.i18n import template_lang_url, template_translate
from app.pagination import page_url


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["_"] = template_translate
templates.env.globals["lang_url"] = template_lang_url
templates.env.globals["available_users"] = template_available_users
templates.env.globals["current_roles"] = template_current_roles
templates.env.globals["current_user"] = template_current_user
templates.env.globals["has_permission"] = template_has_permission
templates.env.globals["page_url"] = page_url
