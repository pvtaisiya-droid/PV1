from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.i18n import template_lang_url, template_translate


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["_"] = template_translate
templates.env.globals["lang_url"] = template_lang_url
