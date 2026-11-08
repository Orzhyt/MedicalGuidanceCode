"""Publish tested drafts, provision private API credentials, export secret-free DSL."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app
from extensions.ext_database import db
from models import Account, App
from models.model import ApiToken, Site
from services.app_dsl_service import AppDslService
from services.workflow_service import WorkflowService

ROOT = Path("/tmp/hospital_guidance")
installed = json.loads((ROOT / "installed.json").read_text())
api_credentials = {}
with app.app_context(), Session(db.engine, expire_on_commit=False) as session:
    for key in ["consultation", "booking"]:
        model = session.get(App, installed[key])
        account = session.get(Account, model.created_by)
        account.set_tenant_id(model.tenant_id)
        published = WorkflowService().publish_workflow(session=session, app_model=model, account=account, marked_name="医院导诊 2.1", marked_comment="明确科室即推荐；只追问影响分诊的信息；给出日期即展示余号；患者与后台内容分开。")
        session.flush()
        model.workflow_id = published.id
        model.enable_api = True
        model.enable_site = key == "consultation"
        api_token = session.scalar(select(ApiToken).where(ApiToken.app_id == model.id, ApiToken.type == "app"))
        if not api_token:
            api_token = ApiToken(app_id=model.id, tenant_id=model.tenant_id, type="app", token=ApiToken.generate_api_key("app-", 32))
            session.add(api_token)
        session.commit()
        api_credentials[key] = api_token.token
        site = session.scalar(select(Site).where(Site.app_id == model.id))
        installed[key + "_site_code"] = site.code if site else None
        installed[key + "_published_workflow"] = published.id
        installed[key + "_name"] = model.name
        (ROOT / "exports" / (key + ".yml")).write_text(AppDslService.export_dsl(app_model=model, include_secret=False), encoding="utf-8")
    (ROOT / "installed.json").write_text(json.dumps(installed, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "private/dify_api_keys.json").write_text(json.dumps(api_credentials), encoding="utf-8")
    print(json.dumps({"published": True, "consultation": installed["consultation"], "booking": installed["booking"], "secrets_exported": False}))

