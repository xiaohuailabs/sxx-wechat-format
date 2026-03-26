#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
API 生图脚本

- 从本技能目录读取 config.json（找不到则回退 config.example.json）
- 从提示词文件读取 YAML 头部：
  - aspect_ratio: 必须
  - image_size: 可选（未提供则用 config.settings.image_size）
- 调用 models/{model}:generateContent（第三方网关兼容 Google 官方 REST 形态）
- 把返回图片落盘（优先 --out；否则写入 config.output_dir）
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import mimetypes
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request


def _eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _read_text(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _read_json(p: pathlib.Path) -> dict:
    return json.loads(_read_text(p))


def _mask(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) <= 8:
        return "***"
    return s[:4] + "..." + s[-4:]


def _strip_known_version_suffix(url: str) -> tuple[str, str | None]:
    """
    如果 base_url 以 /v1 或 /v1beta 结尾，则返回 (root, version)。
    否则返回 (base_url, None)。
    """
    u = (url or "").strip().rstrip("/")
    for v in ("v1beta", "v1alpha", "v1"):
        suf = "/" + v
        if u.endswith(suf):
            return u[: -len(suf)], v
    return u, None


def _candidate_generate_content_urls(base_url: str, model: str, api_version: str | None) -> list[str]:
    root, inferred = _strip_known_version_suffix(base_url)

    versions: list[str] = []
    if api_version and api_version != "auto":
        versions = [api_version]
    elif inferred:
        versions = [inferred]
    else:
        # 官方文档默认 v1beta；再尝试 v1
        versions = ["v1beta", "v1"]

    return [root.rstrip("/") + f"/{v}/models/{model}:generateContent" for v in versions]


def _request_json(url: str, headers: dict, payload: dict, timeout_s: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, method="POST", headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            txt = raw.decode("utf-8", errors="replace")
            try:
                j = json.loads(txt)
            except json.JSONDecodeError:
                j = None
            return {
                "ok": 200 <= resp.status < 300,
                "status": resp.status,
                "url": url,
                "headers": dict(resp.headers.items()),
                "raw_text": txt,
                "json": j,
            }
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        txt = raw.decode("utf-8", errors="replace")
        try:
            j = json.loads(txt) if txt else None
        except json.JSONDecodeError:
            j = None
        return {
            "ok": False,
            "status": getattr(e, "code", None),
            "url": url,
            "headers": dict(getattr(e, "headers", {}).items()),
            "raw_text": txt,
            "json": j,
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "headers": {},
            "raw_text": str(e),
            "json": None,
        }


def _sleep_s(seconds: float):
    if seconds <= 0:
        return
    time.sleep(seconds)


def _guess_mime(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    guess, _ = mimetypes.guess_type(str(path))
    return guess or "application/octet-stream"


def _ext_from_mime(mime: str) -> str:
    m = (mime or "").lower()
    if "png" in m:
        return ".png"
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    if "webp" in m:
        return ".webp"
    return ".bin"


def _parse_frontmatter_and_body(text: str) -> tuple[dict, str]:
    """
    只解析最简单的一层 frontmatter（--- ... ---），够用即可：
      aspect_ratio: "16:9"
      image_size: "4K"
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        k, v = s.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if (len(v) >= 2) and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        meta[k] = v

    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def _normalize_image_size(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.upper()
    if s in ("1K", "2K", "4K"):
        return s
    # 允许写成 1k/2k/4k
    if s in ("1k".upper(), "2k".upper(), "4k".upper()):
        return s
    return s


def _normalize_output_format(v) -> str:
    """
    最终落盘格式：
    - auto: 按 API 回包 mime 原样保存
    - png/jpg/webp: 强制转换并按该格式保存（需要 Pillow）
    """
    if v is None:
        return "auto"
    s = str(v).strip().lower()
    if not s or s == "auto":
        return "auto"
    if s in ("png", "webp"):
        return s
    if s in ("jpg", "jpeg"):
        return "jpg"
    return "auto"


def _normalize_jpg_quality(v) -> int | None:
    if v is None:
        return None
    try:
        q = int(str(v).strip())
    except Exception:
        return None
    # Pillow 的 JPEG quality 通常建议 1~95（100 会更慢且收益不大）
    if q < 1:
        q = 1
    if q > 95:
        q = 95
    return q


def _ext_from_output_format(fmt: str) -> str | None:
    f = (fmt or "").strip().lower()
    if f == "png":
        return ".png"
    if f == "jpg":
        return ".jpg"
    if f == "webp":
        return ".webp"
    return None


def _extract_inline_images(resp_json: dict) -> list[dict]:
    """
    candidates[0].content.parts[*].inlineData = { mimeType, data(base64) }
    """
    if not isinstance(resp_json, dict):
        return []

    cands = resp_json.get("candidates")
    if not isinstance(cands, list) or not cands:
        return []

    c0 = cands[0] if isinstance(cands[0], dict) else {}
    content = c0.get("content") if isinstance(c0.get("content"), dict) else {}
    parts = content.get("parts")
    if not isinstance(parts, list):
        return []

    out: list[dict] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        inline = part.get("inlineData") or part.get("inline_data")
        if not isinstance(inline, dict):
            continue
        b64 = inline.get("data")
        if not isinstance(b64, str) or not b64:
            continue
        mime = inline.get("mimeType") or inline.get("mime_type") or ""
        out.append({"b64": b64, "mime": mime, "thought": bool(part.get("thought"))})
    return out


def _redact_response_json(resp_json: dict) -> dict:
    """
    避免把超大的 base64 写进调试文件：只保留 mimeType/thought，不保留 data。
    """
    if not isinstance(resp_json, dict):
        return resp_json

    j = json.loads(json.dumps(resp_json, ensure_ascii=False))  # 深拷贝（纯 JSON）
    cands = j.get("candidates")
    if not isinstance(cands, list):
        return j

    for cand in cands:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            if "data" in inline:
                inline["data"] = "<base64 已省略>"
    return j


def _build_payload(prompt: str, aspect_ratio: str, image_size: str | None, ref_images: list[pathlib.Path]) -> dict:
    parts: list[dict] = [{"text": prompt}]

    for p in ref_images:
        b = p.read_bytes()
        parts.append(
            {
                "inlineData": {
                    "mimeType": _guess_mime(p),
                    "data": base64.b64encode(b).decode("ascii"),
                }
            }
        )

    gen_cfg: dict = {
        "responseModalities": ["TEXT", "IMAGE"],
        "imageConfig": {"aspectRatio": aspect_ratio},
    }
    if image_size:
        gen_cfg["imageConfig"]["imageSize"] = image_size

    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_cfg,
    }


def _load_config(config_path: pathlib.Path) -> dict:
    """
    优先读取 config.json；不存在则回退到 config.example.json（便于独立分享场景）。
    """
    if config_path.exists():
        cfg = _read_json(config_path)
        if isinstance(cfg, dict):
            return cfg
        raise SystemExit(f"配置文件不是 JSON 对象：{config_path}")

    example = config_path.parent / "config.example.json"
    if example.exists():
        _eprint(f"提示：未找到 config.json，正在使用示例配置：{example}")
        cfg = _read_json(example)
        if isinstance(cfg, dict):
            return cfg
        raise SystemExit(f"示例配置不是 JSON 对象：{example}")

    raise SystemExit(f"未找到配置文件：{config_path}（也不存在 config.example.json）")


def _get_cfg(cfg: dict) -> tuple[str, dict, dict]:
    output_dir = cfg.get("output_dir")
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    secrets = cfg.get("secrets") if isinstance(cfg.get("secrets"), dict) else {}

    if not isinstance(output_dir, str) or not output_dir.strip():
        raise SystemExit("配置缺少 output_dir（字符串）")
    return output_dir, settings, secrets


def _ensure_parent(p: pathlib.Path):
    p.parent.mkdir(parents=True, exist_ok=True)


def _write_bytes(p: pathlib.Path, data: bytes):
    _ensure_parent(p)
    p.write_bytes(data)


def _try_convert_image_bytes(img_bytes: bytes, out_path: pathlib.Path, jpg_quality: int | None = None) -> bool:
    """
    尝试用 Pillow 把 img_bytes 转换成 out_path 的格式并保存。
    成功返回 True；失败返回 False（调用方可回退到“直接写 bytes”）。
    """
    suf = out_path.suffix.lower().lstrip(".")
    if not suf:
        return False
    if suf == "jpeg":
        suf = "jpg"
    if suf not in ("png", "jpg", "webp"):
        return False

    try:
        from PIL import Image  # type: ignore
        import io
    except Exception:
        return False

    try:
        im = Image.open(io.BytesIO(img_bytes))
        _ensure_parent(out_path)

        if suf == "jpg":
            # JPEG 不支持 alpha：常见回包可能是 RGBA/LA/P，需要转成 RGB。
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg.convert("RGB")
            else:
                im = im.convert("RGB")

            save_kwargs: dict = {}
            if jpg_quality is not None:
                save_kwargs["quality"] = int(jpg_quality)
                save_kwargs["optimize"] = True
            im.save(str(out_path), format="JPEG", **save_kwargs)
            return True

        fmt = suf.upper()
        im.save(str(out_path), format=fmt)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="API 生图：调用 Gemini generateContent 接口生成图片并落盘。")
    ap.add_argument("--config", default=None, help="配置文件路径（默认：本技能目录下 config.json）")

    ap.add_argument("--prompt-file", default=None, help="提示词文件路径（建议：带 YAML 头部）")
    ap.add_argument("--prompt", default=None, help="提示词文本（调试用）")
    ap.add_argument("--reference", nargs="*", default=[], help="参考图路径（可选，支持多张）")

    ap.add_argument("--out", default=None, help="输出图片路径（可选；未提供则写入 output_dir）")

    # 调试兜底：一般不要用，优先写到提示词 YAML 头部。
    ap.add_argument("--aspect-ratio", default=None, help="图片比例（调试兜底；优先从提示词文件读取）")
    ap.add_argument("--image-size", default=None, help="分辨率（调试兜底；优先从提示词文件读取）")

    args = ap.parse_args()

    skill_dir = pathlib.Path(__file__).resolve().parent.parent
    config_path = pathlib.Path(args.config).expanduser().resolve() if args.config else (skill_dir / "config.json")
    cfg = _load_config(config_path)
    output_dir, settings, secrets = _get_cfg(cfg)

    base_url = str(settings.get("base_url") or "").strip()
    model = str(settings.get("model") or "").strip()
    api_key = str(secrets.get("api_key") or "").strip()

    if not base_url:
        raise SystemExit("配置缺少 settings.base_url")
    if not model:
        raise SystemExit("配置缺少 settings.model")
    if not api_key:
        raise SystemExit("配置缺少 secrets.api_key（请在网页面板里填写）")

    timeout_s = int(settings.get("timeout_s") or 120)
    max_retries = int(settings.get("max_retries") or 0)
    retry_backoff_s = float(settings.get("retry_backoff_s") or 0)

    auth_mode = str(settings.get("auth_mode") or "auto").strip() or "auto"
    api_version = str(settings.get("api_version") or "auto").strip() or "auto"
    save_response_json = bool(settings.get("save_response_json"))
    save_thought_images = bool(settings.get("save_thought_images"))
    output_format = _normalize_output_format(settings.get("output_format"))
    jpg_quality = _normalize_jpg_quality(settings.get("jpg_quality"))
    desired_ext = _ext_from_output_format(output_format)

    prompt_text = ""
    meta: dict = {}

    prompt_file = pathlib.Path(args.prompt_file).expanduser().resolve() if args.prompt_file else None
    if prompt_file:
        raw = _read_text(prompt_file)
        meta, prompt_text = _parse_frontmatter_and_body(raw)
    else:
        prompt_text = (args.prompt or "").strip()

    if not prompt_text.strip():
        raise SystemExit("提示词为空：请提供 --prompt-file 或 --prompt")

    aspect_ratio = str(meta.get("aspect_ratio") or "").strip() or (str(args.aspect_ratio or "").strip())
    if not aspect_ratio:
        raise SystemExit("缺少 aspect_ratio：请在提示词文件 YAML 头部写 aspect_ratio（推荐），或临时传 --aspect-ratio 兜底。")

    image_size = _normalize_image_size(str(meta.get("image_size") or "").strip() or (args.image_size or None))
    if not image_size:
        image_size = _normalize_image_size(settings.get("image_size"))

    if image_size and image_size not in ("1K", "2K", "4K"):
        _eprint(f"警告：image_size={image_size} 不是 1K/2K/4K，仍将尝试提交（可能被 API 拒绝）")

    ref_images = [pathlib.Path(p).expanduser().resolve() for p in (args.reference or [])]
    for p in ref_images:
        if not p.exists() or not p.is_file():
            raise SystemExit(f"参考图不存在：{p}")

    payload = _build_payload(prompt=prompt_text, aspect_ratio=aspect_ratio, image_size=image_size, ref_images=ref_images)

    urls = _candidate_generate_content_urls(base_url=base_url, model=model, api_version=api_version)
    if not urls:
        raise SystemExit("无法从 base_url 生成请求地址，请检查 settings.base_url")

    auth_attempts = ["google", "bearer"] if auth_mode == "auto" else [auth_mode]
    if auth_mode not in ("auto", "google", "bearer"):
        _eprint(f"警告：未知 auth_mode={auth_mode}，按 auto 处理")
        auth_attempts = ["google", "bearer"]

    _eprint(f"使用 base_url={base_url} model={model} api_key={_mask(api_key)}")
    _eprint(
        f"aspect_ratio={aspect_ratio} image_size={image_size or 'DEFAULT'} "
        f"reference={len(ref_images)} output_format={output_format} jpg_quality={jpg_quality if jpg_quality is not None else 'DEFAULT'}"
    )

    final = None
    used = {"url": None, "auth": None}

    for url in urls:
        for auth in auth_attempts:
            headers = {"Accept": "application/json"}
            if auth == "google":
                headers["x-goog-api-key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"

            attempt = 0
            while True:
                r = _request_json(url=url, headers=headers, payload=payload, timeout_s=timeout_s)
                final = r

                status = r.get("status")
                # 404：尝试下一个版本 URL
                if status == 404:
                    break
                # 401/403：尝试下一个鉴权方式
                if status in (401, 403):
                    break

                if r.get("ok"):
                    used = {"url": url, "auth": auth}
                    break

                # 可重试：网络错误/429/5xx
                retriable = (status is None) or (status == 429) or (isinstance(status, int) and 500 <= status <= 599)
                if retriable and attempt < max_retries:
                    wait_s = retry_backoff_s * (2**attempt) if retry_backoff_s > 0 else (1.0 * (2**attempt))
                    attempt += 1
                    _eprint(f"请求失败（status={status}），准备重试 {attempt}/{max_retries}，等待 {wait_s:.1f}s ...")
                    _sleep_s(wait_s)
                    continue

                used = {"url": url, "auth": auth}
                break

            if final and final.get("ok"):
                break
        if final and final.get("ok"):
            break

    if final is None:
        raise SystemExit("请求未执行（异常）")

    status = final.get("status")
    if not final.get("ok"):
        _eprint(f"请求失败：status={status} url={final.get('url')}")
        _eprint((final.get("raw_text") or "")[:2000])
        raise SystemExit(2)

    imgs = _extract_inline_images(final.get("json"))
    if not imgs:
        raise SystemExit("未在回包中找到 inlineData 图片（请检查 prompt/模型/网关）")

    if not save_thought_images:
        finals = [x for x in imgs if not x.get("thought")]
        imgs_to_save = finals if finals else imgs
    else:
        imgs_to_save = imgs

    # 输出路径/格式：
    # - settings.output_format=auto：按回包 mime 原样保存（png/jpg/webp）
    # - settings.output_format=png/jpg/webp：强制转换并按该格式落盘（需要 Pillow；jpg 可用 settings.jpg_quality 控制压缩率）
    # - 指定 --out（带后缀）：优先按 --out 后缀落盘；必要时尝试转换（jpg 同样会应用 settings.jpg_quality）
    if args.out:
        base_out = pathlib.Path(args.out).expanduser()
        base_has_suffix = bool(base_out.suffix)
    else:
        out_root = pathlib.Path(os.path.expanduser(output_dir)).resolve()
        name = prompt_file.stem if prompt_file else ("generated-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        base_out = out_root / name  # 不带后缀；后缀由回包 mime 决定
        base_has_suffix = False

    out_paths: list[pathlib.Path] = []
    for idx, item in enumerate(imgs_to_save, start=1):
        mime = str(item.get("mime") or "")
        mime_ext = _ext_from_mime(mime)
        ext = desired_ext or mime_ext
        if base_has_suffix:
            if idx == 1:
                out_paths.append(base_out)
            else:
                out_paths.append(base_out.with_name(f"{base_out.stem}-{idx:03d}{base_out.suffix}"))
        else:
            parent = base_out.parent
            stem = base_out.name
            if idx == 1:
                out_paths.append(parent / (stem + ext))
            else:
                out_paths.append(parent / (stem + f"-{idx:03d}" + ext))

    saved_paths: list[pathlib.Path] = []
    for item, target in zip(imgs_to_save, out_paths):
        img_bytes = base64.b64decode("".join(str(item.get("b64") or "").split()))
        mime_ext = _ext_from_mime(str(item.get("mime") or ""))
        target_ext = (target.suffix or "").lower()
        wants_jpg_quality = (jpg_quality is not None) and (
            (output_format == "jpg") or (bool(args.out) and target_ext in (".jpg", ".jpeg"))
        )

        # 1) 默认 auto 且不需要 JPEG 重新编码：能直接写就直接写，避免不必要依赖 Pillow。
        if output_format == "auto" and not wants_jpg_quality:
            if target_ext and (mime_ext != ".bin") and (target_ext != mime_ext.lower()):
                # 用户明确指定了后缀且与回包不一致：尝试转换；转换失败再回退保存原格式。
                if _try_convert_image_bytes(img_bytes, target, jpg_quality if wants_jpg_quality else None):
                    saved_paths.append(target)
                    continue

                final_path = target.with_suffix(mime_ext)
                if final_path != target:
                    _eprint(f"提示：无法转换为 {target.suffix}，将按回包格式保存为：{final_path}")
                _write_bytes(final_path, img_bytes)
                saved_paths.append(final_path)
                continue

            _write_bytes(target, img_bytes)
            saved_paths.append(target)
            continue

        # 2) 强制输出格式，或需要按 quality 重新编码 JPEG：优先尝试转换/重编码。
        if (output_format != "auto") and (not wants_jpg_quality) and target_ext and (mime_ext != ".bin") and (target_ext == mime_ext.lower()):
            # 目标格式与回包一致且不需要重新编码：直接写 bytes 即可。
            _write_bytes(target, img_bytes)
            saved_paths.append(target)
            continue

        if _try_convert_image_bytes(img_bytes, target, jpg_quality if wants_jpg_quality else None):
            saved_paths.append(target)
            continue

        # 转换失败：回退为按回包格式写入，并尽量修正后缀。
        final_path = target
        if target_ext and mime_ext != ".bin" and target_ext != mime_ext.lower():
            final_path = target.with_suffix(mime_ext)
            if final_path != target:
                _eprint(f"提示：无法转换为 {target.suffix}，将按回包格式保存为：{final_path}")

        _write_bytes(final_path, img_bytes)
        saved_paths.append(final_path)

    saved = [str(p) for p in saved_paths]

    # 可选：保存调试回包（注意：不写入 base64）
    if save_response_json:
        primary_out = saved_paths[0]
        resp_path = primary_out.with_suffix(primary_out.suffix + ".response.json")
        req_id = ""
        headers = final.get("headers") or {}
        if isinstance(headers, dict):
            for k, v in headers.items():
                if str(k).lower() in ("x-oneapi-request-id", "x-request-id"):
                    req_id = str(v)
                    break
        dbg = {
            "request": {
                "used_url": used.get("url") or final.get("url"),
                "auth_mode": used.get("auth") or auth_mode,
                "model": model,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "prompt_file": (str(prompt_file) if prompt_file else None),
                "reference_files": [str(p) for p in ref_images],
            },
            "response": {
                "status": status,
                "request_id": req_id,
                "headers": headers,
                "json": _redact_response_json(final.get("json") or {}),
                "raw_text_preview": (final.get("raw_text") or "")[:2000],
            },
            "saved_images": saved,
        }
        _ensure_parent(resp_path)
        resp_path.write_text(json.dumps(dbg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 输出摘要
    req_id = ""
    headers = final.get("headers") or {}
    if isinstance(headers, dict):
        for k, v in headers.items():
            if str(k).lower() in ("x-oneapi-request-id", "x-request-id"):
                req_id = str(v)
                break

    print("生图完成")
    print(f"- 状态码: {status}")
    if used.get("url"):
        print(f"- 请求地址: {used.get('url')}")
    if req_id:
        print(f"- 请求ID: {req_id}")
    print(f"- 保存: {len(saved)} 张")
    for s in saved:
        print(f"  - {s}")


if __name__ == "__main__":
    t0 = time.time()
    try:
        main()
    finally:
        _eprint(f"Done in {time.time() - t0:.2f}s")
