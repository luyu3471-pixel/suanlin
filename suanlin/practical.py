"""Bounded processing of text supplied by the operator."""
import base64
from collections import Counter
import csv
import hashlib
import io
import ipaddress
import json
import math
import re
import zipfile

MAX_INPUT_BYTES = 256 * 1024
MAX_FILES = 12
NAME_PATTERN = re.compile(r'[^/\\:\x00-\x1f]{1,120}')
ALERT = re.compile(r'\berror\b|\bfatal\b|\bexception\b|\btimeout\b|\bwarn(?:ing)?\b|超时|失败|异常|错误|告警|断线', re.I)
ACCESS = re.compile(r'"\S+\s+[^"]+\s+HTTP/[^"]+"\s+([1-5]\d{2})(?:\s|$)')
STATUS = re.compile(r'(?:\b(?:http_status|status_code|status)\b|状态码)\s*[:=]\s*([1-5]\d{2})\b', re.I)
SERVICE = re.compile(r'(?:\b(?:service|device|component)\b|服务|设备)\s*[:=]\s*([^\s,;|]+)', re.I)
LATENCY = re.compile(r'(?:latency_ms|duration_ms|elapsed_ms|耗时)\s*[:=]\s*(\d+(?:\.\d+)?)', re.I)
SECRET_PATTERNS = {
    '邮箱': re.compile(r'(?<![\w.+-])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+', re.I),
    '手机号': re.compile(r'(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)'),
    '身份证号': re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'),
    'IPv4 地址': re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])'),
}


def validate_input(kind, source):
    if not isinstance(source, dict) or set(source) - {'files', 'query', 'fields', 'example'}:
        raise ValueError('请提供文件或粘贴文字')
    files = source.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('每个任务支持 1 至 12 个文件')
    clean, total = [], 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'name', 'text'}:
            raise ValueError('输入文件格式无效')
        name, text = item['name'], item['text']
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or name.strip() in ('', '.', '..'):
            raise ValueError('文件名无效')
        if not isinstance(text, str) or not text.strip() or '\x00' in text:
            raise ValueError('请使用非空的 UTF-8 文本文件')
        total += len(text.encode('utf-8'))
        if total > MAX_INPUT_BYTES:
            raise ValueError('输入合计最多 256 KB，可先截取需要的范围')
        clean.append({'name': name.strip(), 'text': text.removeprefix('\ufeff')})
    if len({f['name'].casefold() for f in clean}) != len(clean):
        raise ValueError('请为同名文件改名后再提交')
    if kind != 'document_search' and len(clean) != 1:
        raise ValueError('日志分析和脱敏任务每次处理一个文件')
    result = {'files': clean}
    if source.get('example') is True:
        result['example'] = True
    if kind == 'document_search':
        query = source.get('query')
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
            raise ValueError('请填写 1 至 120 字的检索内容')
        result['query'] = query.strip()
    if kind == 'archive':
        fields = source.get('fields', [])
        if not isinstance(fields, list) or len(fields) > 16 or any(
                not isinstance(f, str) or not 1 <= len(f.strip()) <= 48 for f in fields):
            raise ValueError('可指定最多 16 个字段，每个字段最多 48 字')
        result['fields'] = list(dict.fromkeys(f.strip().casefold() for f in fields))
    return result


def input_metadata(spec):
    source = spec['input']
    return {'files': [{'name': f['name'], 'bytes': len(f['text'].encode())} for f in source['files']],
            **{k: source[k] for k in ('query', 'fields', 'example') if k in source}}


def input_digest(spec):
    return hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _value(row, *keys):
    return next((row[k] for k in keys if k in row and row[k] not in ('', None)), '')


def _numeric(value, integer=False):
    try:
        num = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(num) or num < 0 or (integer and not num.is_integer()):
        return None
    return int(num) if integer else num


def _csv_reader(text):
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader, [])
        if not header or any(not name.strip() for name in header):
            raise ValueError('CSV 缺少有效的表头')
        rows = []
        while True:
            line_no = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f'CSV 第 {line_no} 行的列数与表头不一致')
            rows.append((line_no, row))
        return header, rows
    except csv.Error as exc:
        raise ValueError('CSV 格式无效，请检查引号和分隔符') from exc


def _log_rows(file):
    text = file['text']
    if file['name'].lower().endswith('.csv'):
        header, rows = _csv_reader(text)
        names = [name.strip().casefold() for name in header]
        if len(set(names)) != len(names):
            raise ValueError('日志 CSV 中的列名不能重复')
        for line_no, values in rows:
            row = dict(zip(names, values))
            message = _value(row, 'message', 'msg', 'error', '日志', '消息')
            yield line_no, row, str(message)
        return
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        row = {}
        if line.lstrip().startswith('{') or file['name'].lower().endswith(('.jsonl','.ndjson')):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    row = {k.casefold(): v for k, v in parsed.items()}
                else:
                    raise ValueError()
            except (ValueError, RecursionError) as exc:
                if file['name'].lower().endswith(('.jsonl','.ndjson')):
                    raise ValueError(f'第 {line_no} 行不是有效的 JSON 对象') from exc
        message = _value(row, 'message', 'msg', 'error', '日志', '消息')
        yield line_no, row, str(message) if message else line


def analyze(source, progress):
    file = source['files'][0]
    counts, alerts, groups, latencies, examples = Counter(), Counter(), Counter(), [], []
    records = abnormal = 0
    total = len(file['text'].splitlines())
    for number, row, message in _log_rows(file):
        if records % 100 == 0:
            progress('read_logs', min(number - 1,total), total)
        records += 1
        access = ACCESS.search(message) or STATUS.search(message)
        status = _numeric(_value(row,'status','status_code','http_status','状态码') or (access[1] if access else ''), True)
        if status is not None and 100 <= status <= 599:
            counts[str(status)] += 1
        latency_match = LATENCY.search(message)
        latency_value = _value(row, 'latency_ms','duration_ms','elapsed_ms','耗时')
        latency = _numeric(latency_value if latency_value != '' else (latency_match[1] if latency_match else ''))
        if latency is not None:
            latencies.append(latency)
        level = str(_value(row, 'level','severity','日志级别')).casefold()
        terms = {word.casefold() for word in ALERT.findall(message)}
        if terms or level in ('error','fatal','warn','warning','critical') or (status is not None and 400 <= status <= 599):
            abnormal += 1
            alerts.update(terms or {level or ('HTTP '+str(status))})
            service = SERVICE.search(message)
            group = str(_value(row, 'service','device','host','component','服务','设备') or (service[1] if service else ''))[:100]
            if group:
                groups[group] += 1
            if len(examples) < 20:
                examples.append({'line':number, 'status':status, 'service':group, 'excerpt':message[:260]})
    if not records:
        raise ValueError('文件里没有可以分析的日志记录')
    progress('read_logs',total,total)
    latencies.sort()
    return {'records':records,'abnormal':abnormal,'status_counts':dict(sorted(counts.items())),
            'alerts':dict(alerts.most_common(10)),
            'affected':[{'name':name,'count':count} for name,count in groups.most_common(10)],
            'p95_latency_ms':latencies[math.ceil(len(latencies)*.95)-1] if latencies else None,
            'latency_samples':len(latencies),'examples':examples}


def search(source, progress):
    query = source['query']
    words = list(dict.fromkeys(re.findall(r'[a-z0-9_.-]+|[\u3400-\u9fff]+',query.casefold()))) or [query.casefold()]
    pairs = {word[i:i+2] for word in words if len(word)>2 and re.fullmatch(r'[\u3400-\u9fff]+',word)
             for i in range(len(word)-1)} - {'怎么','如何','哪些','什么','是否','可以','需要','的是'}
    hits = []
    for done, file in enumerate(source['files']):
        progress('search_files',done,len(source['files']))
        lines = file['text'].splitlines()
        file_hits = []
        for i, line in enumerate(lines):
            if i % 100 == 0:
                progress('search_files',done,len(source['files']))
            for offset in range(0,len(line),320):
                passage = line[offset:offset+400]
                text = passage.casefold()
                exact = [word for word in words if word in text]
                partial = sorted(pair for pair in pairs if pair in text)
                score = len(exact)*8 + len(partial)
                if not score:
                    continue
                prefix = lines[i-1].strip()[-75:] + '\n' if i and not offset and lines[i-1].strip() else ''
                suffix = '\n' + lines[i+1].strip()[:75] if i+1<len(lines) and offset+400>=len(line) and lines[i+1].strip() else ''
                file_hits.append((score, {'file':file['name'],'line':i+1,'excerpt':prefix+passage.strip()+suffix,
                                          'matched':exact or partial[:6]}))
        hits.extend(sorted(file_hits,key=lambda item:-item[0])[:10])
    progress('search_files',len(source['files']),len(source['files']))
    hits.sort(key=lambda item:(-item[0],item[1]['file'].casefold(),item[1]['line']))
    return {'documents':len(source['files']),'query':query,'matches':[item[1] for item in hits[:12]]}


def redact(source, progress):
    file = source['files'][0]
    counts = Counter()
    fields = set(source['fields'])
    found = set()

    def mask(text):
        for label, pattern in SECRET_PATTERNS.items():
            def replace(match):
                if label == 'IPv4 地址':
                    try:
                        ipaddress.IPv4Address(match[0])
                    except ValueError:
                        return match[0]
                counts[label] += 1
                return '[已脱敏]'
            text = pattern.sub(replace, text)
        return text

    def mask_field(name, value):
        found.add(name)
        if value is None or value == '':
            return value
        counts['字段：'+name] += 1
        return '[已脱敏]'

    def walk(value, depth=0):
        if depth > 32:
            raise ValueError('JSON 嵌套过深，请简化输入')
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                masked = mask(key)
                candidate, index = masked, 2
                while candidate in result:
                    candidate = masked + '_' + str(index)
                    index += 1
                result[candidate] = mask_field(key.casefold(),item) if key.casefold() in fields else walk(item,depth+1)
            return result
        if isinstance(value, list):
            return [walk(item, depth+1) for item in value]
        if isinstance(value, str):
            return mask(value)
        if isinstance(value, int) and not isinstance(value,bool):
            text = str(value)
            masked = mask(text)
            return value if masked == text else masked
        return value

    def plain(text):
        for field in sorted(fields, key=lambda name: (-len(name), name)):
            pattern = re.compile(r'(?<![\w])('+re.escape(field)+r'\s*[:=：]\s*)([^,，;；\r\n]+)', re.I)
            text = pattern.sub(lambda m: m[1]+mask_field(field, m[2]), text)
        return mask(text)

    text = file['text']
    suffix = file['name'].lower().rsplit('.',1)[-1]
    progress('redact',0,1)
    if suffix == 'csv':
        header, rows = _csv_reader(text)
        names = [name.strip().casefold() for name in header]
        found.update(fields & set(names))
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        def safe(cell):
            return "'"+cell if cell.lstrip().startswith(('=','+','-','@')) or cell.startswith(('\t','\r','\n')) else cell
        writer.writerow(safe(mask(cell)) for cell in header)
        for i, (_, row) in enumerate(rows):
            if i%100 == 0:
                progress('redact',i,len(rows))
            writer.writerow(safe(mask_field(name,cell) if name in fields else mask(cell))
                            for name,cell in zip(names,row))
        cleaned, total = output.getvalue(), len(rows)
        filename = 'redacted.csv'
    elif suffix in ('json','jsonl','ndjson'):
        try:
            if suffix == 'json':
                parsed = json.loads(text)
                cleaned = json.dumps(walk(parsed),ensure_ascii=False,indent=2,allow_nan=False)
                total = len(parsed) if isinstance(parsed,list) else 1
                filename = 'redacted.json'
            else:
                lines = [line for line in text.splitlines() if line.strip()]
                total = len(lines)
                records = []
                for i, line in enumerate(lines):
                    if i%100 == 0:
                        progress('redact',i,total)
                    records.append(json.dumps(walk(json.loads(line)),ensure_ascii=False,allow_nan=False))
                cleaned = '\n'.join(records)+'\n'
                filename = 'redacted.jsonl'
        except (ValueError, RecursionError) as exc:
            raise ValueError('JSON 格式无效或嵌套过深，请检查文件') from exc
    else:
        lines = text.splitlines(keepends=True)
        total = len(lines)
        parts = []
        for i,line in enumerate(lines):
            if i%100 == 0:
                progress('redact',i,total)
            parts.append(plain(line))
        cleaned, filename = ''.join(parts), 'redacted.txt'
    progress('pack_archive',0,1)
    result = io.BytesIO()
    info = zipfile.ZipInfo(filename)
    info.compress_type = zipfile.ZIP_DEFLATED
    output = cleaned.encode('utf-8-sig' if suffix == 'csv' else 'utf-8')
    with zipfile.ZipFile(result,'w') as archive:
        archive.writestr(info, output)
    zipped = result.getvalue()
    if len(zipped) > 420 * 1024:
        raise ValueError('压缩结果超过下载上限，请拆分文件后重试')
    progress('pack_archive',1,1)
    return ({'records':total,'redactions':dict(counts),'redacted_count':sum(counts.values()),
             'unmatched_fields':sorted(fields-found),'filename':filename,'uncompressed_bytes':len(output),
             'compressed_bytes':len(zipped),'artifact_retained':True},
            base64.b64encode(zipped).decode('ascii'))
