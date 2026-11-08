"""Run in the existing API container. Reuses exact app/dataset names and preserves other apps."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app
from core.rag.datasource.keyword.jieba.jieba import Jieba
from extensions.ext_database import db
from models import Account, App
from models.account import TenantAccountJoin
from models.dataset import Dataset, Document, DocumentSegment
from services.app_dsl_service import AppDslService, ImportStatus

ROOT = Path("/tmp/hospital_guidance")
NAME = "医院导诊知识库"

with app.app_context(), Session(db.engine, expire_on_commit=False) as session:
    account = session.scalar(select(Account).order_by(Account.created_at))
    if not account:
        raise RuntimeError("Existing account missing; do not initialize a new installation")
    join = session.scalar(select(TenantAccountJoin).where(TenantAccountJoin.account_id == account.id).order_by(TenantAccountJoin.current.desc()))
    account.set_tenant_id(join.tenant_id)
    dataset = session.scalar(select(Dataset).where(Dataset.tenant_id == join.tenant_id, Dataset.name.in_([NAME, "医院导诊教学知识库"])))
    if not dataset:
        dataset = Dataset(tenant_id=join.tenant_id, name=NAME, description="症状分诊、科室推荐和就诊提醒。", provider="vendor", permission="all_team_members", data_source_type="upload_file", indexing_technique="economy", keyword_number=20, created_by=account.id, updated_by=account.id, retrieval_model={"search_method": "keyword_search", "reranking_enable": False, "reranking_model": {"reranking_provider_name": "", "reranking_model_name": ""}, "top_k": 6, "score_threshold_enabled": False})
        session.add(dataset)
        session.flush()
    dataset.name = NAME
    dataset.description = "症状追问、科室推荐和急诊提醒的参考资料。"
    document = session.scalar(select(Document).where(Document.dataset_id == dataset.id))
    text = (ROOT / "knowledge/导诊知识库.md").read_text(encoding="utf-8")
    if not document:
        document = Document(tenant_id=join.tenant_id, dataset_id=dataset.id, position=1, data_source_type="upload_file", data_source_info=None, batch=str(uuid.uuid4()), name="导诊知识库.md", created_from="web", created_by=account.id, word_count=len(text), tokens=len(text), indexing_status="completed", completed_at=datetime.utcnow(), doc_form="text_model", doc_language="zh-Hans")
        session.add(document)
        session.flush()
    indexed = []
    for position, part in enumerate(text.split("\n## ")[1:], 1):
        words = re.search(r"关键词：([^\n]+)", part).group(1).split()
        words.extend([part[:3], "导诊"])
        segment = session.scalar(select(DocumentSegment).where(DocumentSegment.document_id == document.id, DocumentSegment.position == position))
        if not segment:
            segment = DocumentSegment(tenant_id=join.tenant_id, dataset_id=dataset.id, document_id=document.id, position=position, content="## " + part, word_count=len(part), tokens=len(part), keywords=words, index_node_id=str(uuid.uuid4()), index_node_hash=hashlib.sha256(part.encode()).hexdigest(), hit_count=0, enabled=True, status="completed", created_by=account.id, updated_by=account.id, indexing_at=datetime.utcnow(), completed_at=datetime.utcnow())
            session.add(segment)
        else:
            segment.content = "## " + part
            segment.keywords = words
            segment.enabled = True
            segment.status = "completed"
        indexed.append((segment, words))
    session.commit()
    index = Jieba(dataset)
    for segment, words in indexed:
        index.update_segment_keywords_index(segment.index_node_id, words)
    credentials = json.loads((ROOT / "private/service_credentials.json").read_text())
    installed = {"dataset_id": dataset.id, "knowledge_segments": len(indexed)}
    for key in ["consultation", "booking"]:
        source = yaml.safe_load((ROOT / (key + ".yml")).read_text(encoding="utf-8"))
        for variable in source["workflow"]["environment_variables"]:
            if variable["name"] == "SERVICE_API_KEY":
                variable["value"] = credentials["api_key"]
        for node in source["workflow"]["graph"]["nodes"]:
            if node["data"]["type"] == "knowledge-retrieval":
                node["data"]["dataset_ids"] = [AppDslService.encrypt_dataset_id(dataset.id, join.tenant_id)]
        existing = session.scalar(select(App).where(App.tenant_id == join.tenant_id, App.name == source["app"]["name"]))
        service = AppDslService(session)
        result = service.import_app(account=account, import_mode="yaml-content", yaml_content=yaml.safe_dump(source, allow_unicode=True), app_id=existing.id if existing else None)
        if result.status == ImportStatus.PENDING.value:
            result = service.confirm_import(import_id=result.id, account=account)
        if result.status == ImportStatus.FAILED.value:
            raise RuntimeError(result.error)
        session.commit()
        installed[key] = result.app_id
    (ROOT / "installed.json").write_text(json.dumps(installed), encoding="utf-8")
    print(json.dumps(installed))
