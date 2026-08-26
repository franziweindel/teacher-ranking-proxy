"""Artifact discovery and verification (PIPELINE.md §7).

Never trusts "we release the data": inspects Hugging Face datasets/models and
GitHub repositories via their public APIs, records exactly which identifiers
were checked and what was found, and asks the runtime LLM (grounded in the
artifact metadata + card text only) whether teacher identity / task ids are
recoverable.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.parse
from typing import Any

from .cache import Cache, cache_key
from .llm import LLMClient
from .prompts import ARTIFACT_SYSTEM, ARTIFACT_VERSION
from .retrieval import Fetcher, html_to_text
from .schemas import validate_artifact_judgment

log = logging.getLogger("litmine.artifacts")

HF_RE = re.compile(r"huggingface\.co/(?:(datasets|models)/)?([\w.\-]+/[\w.\-]+)")
GH_RE = re.compile(r"github\.com/([\w.\-]+/[\w.\-]+)")
HF_ID_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")


def _clean_id(s: str) -> str:
    return s.strip().rstrip("/").removesuffix(".git").split("?")[0].split("#")[0]


def find_artifact_references(text: str) -> dict[str, list[str]]:
    """Extract HF dataset/model ids and GitHub repos mentioned in text."""
    hf_ds, hf_any, gh = [], [], []
    for kind, ident in HF_RE.findall(text):
        ident = _clean_id(ident)
        if ident.split("/")[0] in ("blog", "docs", "spaces", "papers", "collections"):
            continue
        (hf_ds if kind == "datasets" else hf_any).append(ident)
    for repo in GH_RE.findall(text):
        repo = _clean_id(repo)
        if repo.split("/")[0].lower() in ("features", "topics", "orgs", "settings"):
            continue
        gh.append(repo)
    dedup = lambda xs: list(dict.fromkeys(xs))
    return {"hf_datasets": dedup(hf_ds), "hf_unknown": dedup(hf_any), "github": dedup(gh)}


class ArtifactVerifier:
    def __init__(self, fetcher: Fetcher, cache: Cache):
        self.f = fetcher
        self.cache = cache

    # ---- Hugging Face --------------------------------------------------------
    def _hf_headers(self) -> dict:
        tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def hf_info(self, repo_id: str, kind: str) -> dict:
        """kind: datasets|models. Returns {exists, accessible, id, files, tags,
        configs, card_text, url, error}."""
        repo_id = _clean_id(repo_id)
        key = cache_key(repo_id=repo_id, kind=kind, v=2)
        hit = self.cache.get("artifact_hf", key)
        if hit is not None:
            return hit
        url = f"https://huggingface.co/api/{kind}/{repo_id}"
        rec: dict[str, Any] = {"kind": kind, "id": repo_id, "exists": False, "accessible": False,
                               "url": f"https://huggingface.co/{'datasets/' if kind == 'datasets' else ''}{repo_id}",
                               "files": [], "tags": [], "configs": [], "card_text": "", "error": None}
        try:
            status, body, _ = self.f.get(url, headers=self._hf_headers())
        except ConnectionError as e:
            rec["error"] = str(e)
            return rec  # not cached: transient
        if status == 200:
            info = json.loads(body)
            rec.update(exists=True, accessible=True, tags=info.get("tags") or [],
                       canonical_id=info.get("id") or repo_id,
                       files=[s.get("rfilename") for s in info.get("siblings") or []],
                       private=info.get("private", False), gated=info.get("gated", False),
                       downloads=info.get("downloads"), sha=info.get("sha"),
                       last_modified=info.get("lastModified"))
            cd = info.get("cardData") or {}
            cfgs = cd.get("configs") or []
            rec["configs"] = [c.get("config_name") for c in cfgs if isinstance(c, dict)] if isinstance(cfgs, list) else []
            # card / README
            for fn in ("README.md",):
                if fn in rec["files"]:
                    raw_url = f"https://huggingface.co/{'datasets/' if kind == 'datasets' else ''}{repo_id}/raw/main/{fn}"
                    try:
                        st, b, _ = self.f.get(raw_url, headers=self._hf_headers())
                        if st == 200:
                            rec["card_text"] = b.decode("utf-8", errors="replace")[:30000]
                    except ConnectionError:
                        pass
        elif status in (401, 403):
            rec.update(exists=True, accessible=False, error=f"HTTP {status} (gated/private)")
        elif status == 404:
            rec["error"] = "HTTP 404 (not found)"
        else:
            rec["error"] = f"HTTP {status}"
        return self.cache.put("artifact_hf", key, rec)

    def _hf_list(self, params: dict) -> list[str]:
        key = cache_key(params=sorted(params.items()), v=1)
        hit = self.cache.get("artifact_hf_list", key)
        if hit is None:
            url = "https://huggingface.co/api/datasets?" + urllib.parse.urlencode(params)
            try:
                hit = {"ids": [d.get("id") for d in self.f.get_json(url, headers=self._hf_headers())]}
            except Exception as e:
                hit = {"ids": [], "error": str(e)}
            self.cache.put("artifact_hf_list", key, hit)
        return hit["ids"]

    def hf_siblings(self, repo_id: str, canonical_id: str | None = None) -> list[str]:
        """Datasets whose name shares the prefix of repo_id up to a hyphenated
        group (Org/Foo-Traj-DeepSeek-15k -> Org/Foo-Traj-*). Looks at the
        canonical author (orgs get renamed) and falls back to a name search.
        Used to find per-teacher / combined trajectory releases."""
        cid = canonical_id or repo_id
        author, name = cid.split("/", 1) if "/" in cid else ("", cid)
        parts = name.split("-")
        found: list[str] = []
        for n in range(len(parts) - 1, 1, -1):          # longer (more specific) prefixes first
            prefix = "-".join(parts[:n])
            ids = self._hf_list({"author": author, "limit": 200}) if author else []
            ids = ids + self._hf_list({"search": prefix, "limit": 100})
            found += [i for i in dict.fromkeys(ids) if i not in (repo_id, cid)
                      and i.split("/", 1)[-1].lower().startswith(prefix.lower() + "-")]
        return list(dict.fromkeys(found))[:12]

    def hf_resolve(self, repo_id: str) -> dict:
        """Unknown kind -> try dataset then model."""
        d = self.hf_info(repo_id, "datasets")
        if d["exists"] and d["accessible"]:
            return d
        m = self.hf_info(repo_id, "models")
        if m["exists"] and (m["accessible"] or not d["exists"]):
            return m
        return d

    # ---- GitHub ----------------------------------------------------------------
    def github_info(self, repo: str) -> dict:
        repo = _clean_id(repo)
        key = cache_key(repo=repo, v=1)
        hit = self.cache.get("artifact_github", key)
        if hit is not None:
            return hit
        hdr = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            hdr["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        rec: dict[str, Any] = {"kind": "github", "id": repo, "url": f"https://github.com/{repo}",
                               "exists": False, "accessible": False, "files": [], "readme_text": "",
                               "error": None, "hf_links": [], "description": ""}
        try:
            status, body, _ = self.f.get(f"https://api.github.com/repos/{repo}", headers=hdr)
        except ConnectionError as e:
            rec["error"] = str(e)
            return rec
        if status == 200:
            info = json.loads(body)
            rec.update(exists=True, accessible=True, description=info.get("description") or "",
                       default_branch=info.get("default_branch"), stars=info.get("stargazers_count"),
                       pushed_at=info.get("pushed_at"))
            branch = info.get("default_branch") or "main"
            try:
                st, b, _ = self.f.get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                                      headers=hdr)
                if st == 200:
                    tree = json.loads(b).get("tree", [])
                    rec["files"] = [t["path"] for t in tree if t.get("type") == "blob"][:2000]
            except ConnectionError:
                pass
            try:
                st, b, _ = self.f.get(f"https://api.github.com/repos/{repo}/readme", headers=hdr)
                if st == 200:
                    content = json.loads(b).get("content", "")
                    rec["readme_text"] = base64.b64decode(content).decode("utf-8", errors="replace")[:30000]
            except (ConnectionError, ValueError):
                pass
            refs = find_artifact_references(rec["readme_text"])
            rec["hf_links"] = refs["hf_datasets"] + refs["hf_unknown"]
        elif status == 404:
            rec["error"] = "HTTP 404 (not found)"
        elif status in (403, 429):
            rec["error"] = f"HTTP {status} (rate limited; set GITHUB_TOKEN)"
            return rec  # don't cache rate-limit failures
        else:
            rec["error"] = f"HTTP {status}"
        return self.cache.put("artifact_github", key, rec)

    def webpage_info(self, url: str) -> dict:
        key = cache_key(url=url, v=1)
        hit = self.cache.get("artifact_web", key)
        if hit is not None:
            return hit
        rec: dict[str, Any] = {"kind": "webpage", "id": url, "url": url, "exists": False,
                               "accessible": False, "text": "", "hf_links": [], "github_links": [],
                               "error": None}
        try:
            status, body, _ = self.f.get(url)
        except ConnectionError as e:
            rec["error"] = str(e)
            return rec
        if status == 200:
            raw = body.decode("utf-8", errors="replace")
            rec.update(exists=True, accessible=True, text=html_to_text(raw)[:30000])
            refs = find_artifact_references(raw)
            rec["hf_links"] = refs["hf_datasets"] + refs["hf_unknown"]
            rec["github_links"] = refs["github"]
        else:
            rec["error"] = f"HTTP {status}"
        return self.cache.put("artifact_web", key, rec)

    # ---- heuristics + LLM judgement --------------------------------------------
    @staticmethod
    def teacher_mentions(info: dict, teachers: list[str]) -> dict[str, list[str]]:
        """Which teacher names appear in file names / configs / card text."""
        hay_files = "\n".join(info.get("files") or []) + "\n" + "\n".join(info.get("configs") or [])
        hay_card = info.get("card_text") or info.get("readme_text") or info.get("text") or ""
        out = {}
        for t in teachers:
            toks = [x for x in re.split(r"[\s\-_/.:]+", t.lower()) if len(x) > 2]
            pat = r"[\s\-_/.:]*".join(re.escape(x) for x in toks) if toks else re.escape(t.lower())
            where = []
            if re.search(pat, hay_files.lower()):
                where.append("files/configs")
            if re.search(pat, hay_card.lower()):
                where.append("card")
            out[t] = where
        return out

    def llm_judge(self, llm: LLMClient, info: dict, paper_description: str, teachers: list[str]) -> dict:
        files = info.get("files") or []
        meta = {"id": info.get("id"), "kind": info.get("kind"), "url": info.get("url"),
                "tags": info.get("tags"), "configs": info.get("configs"),
                "n_files": len(files), "files_sample": files[:300]}
        card = info.get("card_text") or info.get("readme_text") or info.get("text") or ""
        user = (f"PAPER DESCRIPTION OF THE ARTIFACT:\n{paper_description}\n\n"
                f"TEACHER MODELS NAMED IN THE PAPER: {json.dumps(teachers)}\n\n"
                f"ARTIFACT METADATA (JSON):\n{json.dumps(meta, indent=1)}\n\n"
                f"ARTIFACT CARD / README:\n{card[:25000]}")
        res = llm.complete_json("artifact", ARTIFACT_VERSION, ARTIFACT_SYSTEM, user,
                                validator=validate_artifact_judgment, max_tokens=6000)
        return {"judgment": res.parsed, "llm": res.meta()}

    def verify_experiment(self, exp: dict, llm: LLMClient | None, paper_text: str = "") -> dict:
        """Verify every artifact referenced by an extracted experiment.

        Returns {"checked": [...per-artifact records...],
                 "tasks": {"verified": bool|None, "ids": [...]},
                 "trajectories": {"verified": bool|None, "ids": [...], "teacher_separable": ...},
                 "student": {"verified": bool|None, "id": ...}}"""
        teachers = [t["name"] for t in exp.get("teacher_models", [])]
        val = lambda k: (exp.get(k) or {}).get("value")
        result: dict[str, Any] = {"checked": [], "tasks": {"verified": None, "ids": []},
                                  "trajectories": {"verified": None, "ids": [], "teacher_separable": "unclear"},
                                  "student": {"verified": None, "id": val("student_hf_id")}}

        # candidate ids from the extraction; plus HF ids found in linked repos/pages
        traj_ids = [x for x in [val("trajectory_dataset_hf_id")] if x]
        task_ids = [x for x in [val("task_dataset_hf_id")] if x]
        repos = [x for x in [val("environment_repo"), val("github_repo")] if x]
        pages = [x for x in [val("project_page")] if x]
        gh_infos, page_infos = [], []
        for r in repos:
            m = GH_RE.search(r) or (re.match(r"^[\w.\-]+/[\w.\-]+$", r) and re.match(r"^(.*)$", r))
            repo = m.group(1) if m else None
            if not repo:
                continue
            gi = self.github_info(repo)
            gh_infos.append(gi)
            result["checked"].append({k: gi.get(k) for k in ("kind", "id", "url", "exists", "accessible", "error")}
                                     | {"n_files": len(gi.get("files") or [])})
        for p in pages:
            if not p.startswith("http"):
                continue
            pi = self.webpage_info(p)
            page_infos.append(pi)
            result["checked"].append({k: pi.get(k) for k in ("kind", "id", "url", "exists", "accessible", "error")})
        # GitHub repos linked from project pages (papers often omit the literal URL)
        for pi in page_infos:
            for repo in (pi.get("github_links") or [])[:4]:
                if any(g.get("id") == repo for g in gh_infos) or "project-page-template" in repo:
                    continue
                gi = self.github_info(repo)
                gh_infos.append(gi)
                result["checked"].append({k: gi.get(k) for k in ("kind", "id", "url", "exists", "accessible", "error")}
                                         | {"n_files": len(gi.get("files") or []), "role": "discovered_code"})
        discovered = []
        for gi in gh_infos + page_infos:
            discovered += gi.get("hf_links") or []
        discovered = [d for d in dict.fromkeys(discovered) if d not in traj_ids + task_ids]

        def check_hf(hid: str, role: str) -> dict:
            info = self.hf_resolve(hid) if not hid.startswith("http") else \
                self.hf_resolve(HF_RE.search(hid).group(2) if HF_RE.search(hid) else hid)
            rec = {k: info.get(k) for k in ("kind", "id", "url", "exists", "accessible", "error", "canonical_id")}
            rec.update(role=role, n_files=len(info.get("files") or []), configs=info.get("configs"),
                       files=(info.get("files") or [])[:60],
                       teacher_mentions=self.teacher_mentions(info, teachers) if info.get("exists") else {})
            if info.get("exists") and info.get("accessible") and llm is not None and role in ("trajectories", "tasks", "discovered"):
                desc = "; ".join(filter(None, [
                    f"trajectory dataset: {val('trajectory_dataset')}",
                    f"task dataset: {val('task_dataset')}",
                    (exp.get("trajectories_public") or {}).get("evidence", "")]))
                try:
                    rec["llm"] = self.llm_judge(llm, info, desc, teachers)
                    rec["judged_role"] = rec["llm"]["judgment"].get("artifact_role", "unclear")
                except Exception as e:  # keep verification record even if the LLM fails
                    rec["llm_error"] = str(e)
            return rec

        for hid in traj_ids:
            rec = check_hf(hid, "trajectories")
            result["checked"].append(rec)
            result["trajectories"]["ids"].append(rec["id"])
        for hid in task_ids:
            rec = check_hf(hid, "tasks")
            result["checked"].append(rec)
            result["tasks"]["ids"].append(rec["id"])
        for hid in discovered[:8]:
            rec = check_hf(hid, "discovered")
            result["checked"].append(rec)
        # sibling datasets of any verified trajectory set (per-teacher releases)
        seen_ids = {r.get("id") for r in result["checked"]}
        for rec in list(result["checked"]):
            if rec.get("kind") == "datasets" and rec.get("accessible") and \
                    (rec.get("judged_role") or rec.get("role")) == "trajectories":
                for sib in self.hf_siblings(rec["id"], rec.get("canonical_id")):
                    if sib in seen_ids:
                        continue
                    seen_ids.add(sib)
                    srec = check_hf(sib, "discovered")
                    srec["sibling_of"] = rec["id"]
                    result["checked"].append(srec)
        if val("student_hf_id") and HF_ID_RE.match(val("student_hf_id")):
            si = self.hf_info(val("student_hf_id"), "models")
            result["checked"].append({k: si.get(k) for k in ("kind", "id", "url", "exists", "accessible", "error")} | {"role": "student"})
            result["student"]["verified"] = bool(si.get("exists"))

        # aggregate. A "discovered" artifact only counts for a role the LLM
        # assigned to it from its contents; artifacts named by the paper for a
        # role count for that role unless the LLM says they are something else.
        def role_of(r: dict) -> str:
            jr = r.get("judged_role")
            if r.get("role") in ("trajectories", "tasks"):
                return r["role"] if jr in (None, "unclear", r["role"]) else jr
            return jr or "unclear"

        def agg(role: str) -> tuple[bool | None, list[dict]]:
            named = [r for r in result["checked"] if r.get("role") == role]
            matching = [r for r in result["checked"] if r.get("kind") in ("datasets", "models")
                        and role_of(r) == role and r.get("exists") and r.get("accessible")]
            if matching:
                return True, matching
            if named:      # paper named an artifact for this role but it is missing/inaccessible/other
                return False, named
            return None, []

        t_ok, t_recs = agg("trajectories")
        result["trajectories"]["verified"] = t_ok
        if t_ok:
            result["trajectories"]["ids"] = [r["id"] for r in t_recs]
            seps = [((r.get("llm") or {}).get("judgment") or {}).get("checks", {})
                    .get("teacher_subsets_separable", {}).get("decision") for r in t_recs]
            ment = [any(v for v in (r.get("teacher_mentions") or {}).values()) for r in t_recs]
            if "yes" in seps:
                result["trajectories"]["teacher_separable"] = "yes"
            elif seps and all(s == "no" for s in seps if s) and not any(ment):
                result["trajectories"]["teacher_separable"] = "no"
            else:
                result["trajectories"]["teacher_separable"] = "unclear"
            result["trajectories"]["task_ids_recoverable"] = next(
                (((r.get("llm") or {}).get("judgment") or {}).get("checks", {})
                 .get("task_ids_recoverable", {}).get("decision") for r in t_recs
                 if r.get("llm")), "unclear")
        elif t_ok is None:
            gated = [r for r in result["checked"] if r.get("exists") and not r.get("accessible")]
            if gated:
                result["trajectories"]["note"] = "inaccessible/gated artifacts found: " + \
                    ", ".join(r["id"] for r in gated)
        k_ok, k_recs = agg("tasks")
        if k_ok is None and val("environment_repo") and any(
                g.get("exists") and g.get("accessible") for g in gh_infos):
            k_ok, k_recs = True, [r for r in result["checked"] if r.get("kind") == "github" and r.get("exists")]
        result["tasks"]["verified"] = k_ok
        if k_ok:
            result["tasks"]["ids"] = [r["id"] for r in k_recs if r.get("exists")]
        return result
