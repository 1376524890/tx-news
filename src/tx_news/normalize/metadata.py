# Input: HTML bytes（meta/time 标签等）
# Output: 尽力解析得到的 published_at（datetime|None）
# Pos: 发布时间提取器（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from dateutil import parser as dateparser


def try_parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return dateparser.parse(value)
    except Exception:
        pass
    
    # 尝试解析中文日期格式
    try:
        import re
        # 尝试解析 "2026年1月28日" 或 "2026年01月28日"
        match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', value)
        if match:
            year, month, day = match.groups()
            # 检查是否有时分秒
            time_match = re.search(r'(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', value)
            if time_match:
                hour, minute, second = time_match.groups()
                second = second or '0'
                return datetime(
                    int(year), int(month), int(day),
                    int(hour), int(minute), int(second)
                )
            else:
                return datetime(int(year), int(month), int(day))
    except Exception:
        pass
    
    return None


def extract_published_at(html: bytes) -> datetime | None:
    soup = BeautifulSoup(html, "lxml")
    
    # 1. JSON-LD 解析 (application/ld+json)
    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    for script in json_ld_scripts:
        try:
            script_text = script.get_text(strip=True)
            if not script_text:
                continue
            data = json.loads(script_text)
            if isinstance(data, dict):
                for key in ("pubDate", "datePublished", "dateModified", "publishDate"):
                    if key in data and data[key]:
                        dt = try_parse_datetime(data[key])
                        if dt and _is_valid_published_time(dt):
                            return dt
                        elif dt:
                            return None
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for key in ("pubDate", "datePublished", "dateModified", "publishDate"):
                            if key in item and item[key]:
                                dt = try_parse_datetime(item[key])
                                if dt and _is_valid_published_time(dt):
                                    return dt
                                elif dt:
                                    return None
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
    
    # 2. JavaScript 变量提取（actime, pubtime, ptime, publishTime等）
    dt = _extract_datetime_from_script(soup)
    if dt and _is_valid_published_time(dt):
        return dt
    elif dt:
        return None
    
    # 3. HTML 注释提取
    comments = soup.find_all(string=lambda text: isinstance(text, str) and text.strip().startswith("<!--"))
    for comment in comments:
        comment_text = comment.strip()
        if comment_text.startswith("<!--") and comment_text.endswith("-->"):
            comment_text = comment_text[4:-3].strip()
            dt = _extract_datetime_from_text(comment_text)
            if dt and _is_valid_published_time(dt):
                return dt
            elif dt:
                return None
    
    # 4. common meta tags
    for key in (
        ("property", "article:published_time"),
        ("name", "pubdate"),
        ("name", "publishdate"),
        ("name", "publish_date"),
        ("name", "date"),
    ):
        tag = soup.find("meta", attrs={key[0]: key[1]})
        if tag and tag.get("content"):
            dt = try_parse_datetime(tag.get("content"))
            if dt:
                if _is_valid_published_time(dt):
                    return dt
                return None
    
    # 5. <time datetime=...>
    time_tag = soup.find("time")
    if time_tag:
        dt = try_parse_datetime(time_tag.get("datetime") or time_tag.get_text(strip=True))
        if dt:
            if _is_valid_published_time(dt):
                return dt
            return None
    
    # 6. 从特定的HTML标签提取时间（<span>, <em>, <div class="detail-info">等）
    dt = _extract_datetime_from_tags(soup)
    if dt and _is_valid_published_time(dt):
        return dt
    elif dt:
        return None
    
    # 7. 正文日期提取（常见中文日期格式）
    text = soup.get_text()
    dt = _extract_datetime_from_text(text)
    if dt and _is_valid_published_time(dt):
        return dt
    elif dt:
        return None
    
    return None


def _extract_datetime_from_script(soup: BeautifulSoup) -> datetime | None:
    """
    从JavaScript代码中提取发布时间。
    常见变量名：actime, pubtime, ptime, publishTime, createTime, pub_date, date
    支持格式：
    - "actime": "2026-01-28T09:36:04+08"
    - ["actime", "2026-01-28T09:36:04+08"]
    - ['actime', '2026-01-28T09:36:04+08']
    - var date = "2026-01-28..."
    """
    script_tags = soup.find_all("script")
    for script in script_tags:
        script_text = script.get_text()
        
        patterns = [
            r'["\']?(?:actime|pubtime|ptime|publishTime|createTime|pub_date|pubdate|articleTime|date|time)["\']?\s*[:=]\s*["\']([^"\']+?)["\']',
            r'\["(?:actime|pubtime|ptime|publishTime|createTime|pub_date|pubdate|articleTime|date|time)"\s*,\s*["\']([^"\']+?)["\']\]',
            r"\['(?:actime|pubtime|ptime|publishTime|createTime|pub_date|pubdate|articleTime|date|time)'\s*,\s*['\"]([^'\"]+?)['\"]\]",
            r'var\s+\w*date\w*\s*=\s*["\']([^"\']+?)["\']',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, script_text, re.IGNORECASE)
            for match in matches:
                dt = try_parse_datetime(match)
                if dt:
                    return dt
    return None


def _extract_datetime_from_tags(soup: BeautifulSoup) -> datetime | None:
    """
    从特定的HTML标签中提取发布时间。
    例如：<span>2026-01-28 09:37</span>, <em class="f-fl">2026-01-28 09:36:05</em>
    """
    patterns = [
        (r'<span[^>]*>(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)</span>', 'span'),
        (r'<em[^>]*>(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)</em>', 'em'),
        (r'<div[^>]*class="[^"]*detail-info[^"]*"[^>]*>.*?(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)</div>', 'div'),
    ]
    
    html_str = str(soup)
    
    for pattern, tag_name in patterns:
        matches = re.findall(pattern, html_str, re.IGNORECASE | re.DOTALL)
        for match in matches:
            dt = try_parse_datetime(match)
            if dt:
                return dt
    
    return None


def _extract_datetime_from_text(text: str) -> datetime | None:
    patterns = [
        r'\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2}',
        r'\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}',
        r'\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{1,2}:\d{1,2}',
        r'\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{1,2}',
        r'(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{1,2}):(\d{1,2})',
        r'T\d{2}:\d{2}:\d{2}\+[0-9:]+',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if isinstance(match, tuple):
                if len(match) == 6:
                    match_str = f"{match[0]}-{match[1]}-{match[2]} {match[3]}:{match[4]}:{match[5]}"
                else:
                    continue
            else:
                match_str = match
            dt = try_parse_datetime(match_str)
            if dt:
                return dt
    
    # 尝试从正文中提取"X月X日"格式，使用当前年份
    month_day_pattern = r'(\d{1,2})月(\d{1,2})日'
    matches = re.findall(month_day_pattern, text)
    if matches:
        # 取第一个匹配
        month, day = matches[0]
        current_year = datetime.now().year
        date_str = f"{current_year}年{month}月{day}日"
        dt = try_parse_datetime(date_str)
        if dt:
            return dt
    
    return None


def _is_valid_published_time(published_at: datetime, hours: int = 24) -> bool:
    utc8_tz = timezone(timedelta(hours=8))
    now = datetime.now(utc8_tz)
    valid_threshold = now - timedelta(hours=hours)
    
    # 确保published_at有时区信息，如果没有则假设为UTC+8
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=utc8_tz)
    
    return published_at >= valid_threshold
