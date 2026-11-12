"""Check current submission prose against stored evidence, without rerunning benchmarks."""
import hashlib
import json
from pathlib import Path
import re
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]

def main():
    summary=json.loads((ROOT/'results/slot_aware_20261111_summary.json').read_text(encoding='utf-8'))
    correctness=summary['correctness']
    assert correctness['formal_tasks']==450
    assert correctness['accepted_with_warmup']==504
    assert correctness['statuses']=={'completed':450}
    assert correctness['cross_host_differences'], 'Do not remove the unresolved correctness limitation without reviewing evidence'
    retests=[r for r in summary['reports'] if 'retest-b4' in r['directory']]
    assert sum(r['policies']['ai']['submitted'] for r in retests)==42
    final=next(r['policies']['ai'] for r in retests if r['profile']=='throughput')
    assert final['ai_sources']=={'qwen':6,'qwen_guarded':18}
    assert final['changed_model_node_count']==12
    report=(ROOT/'docs/测试与验证报告.md').read_text(encoding='utf-8')
    for r in retests:
        assert f"{r['policies']['ai']['mean_batch_seconds']:.3f}" in report
    reference=next(r for r in summary['reports'] if 'new-b4' in r['directory'] and r['profile']=='throughput')
    for policy in ['fixed_A','fixed_B','smart']:
        assert f"{reference['policies'][policy]['mean_batch_seconds']:.3f}" in report
    editable=['场景设计方案.md','测试与验证报告.md','答辩讲稿与问答.md','AI使用与第三方声明.md']
    prohibited=['未包含办公室用户试点、物理多机','尚未获得用户访谈、外部试点或多物理终端测试的证据','未取得实际管理人员需求访谈、两台物理终端对比','每节点一个执行槽','真实收益仍需多机测试','目前不假称已经完成']
    for name in editable:
        text=(ROOT/'docs'/name).read_text(encoding='utf-8')
        assert '2.4.1' in text and 'Qwen' in text and '压缩' in text,name
        assert not any(old in text for old in prohibited),name
    pdfs=[]
    for filename in ['01-场景设计方案.pdf','02-答辩展示.pdf','03-佐证材料.pdf','05-操作与提交手册.pdf']:
        path=ROOT/'delivery'/filename
        reader=PdfReader(path)
        content='\n'.join(page.extract_text() or '' for page in reader.pages)
        normalized=re.sub(r'\s+','',content)
        assert '2.4.1' in normalized and 'Qwen' in normalized and '压缩' in normalized,filename
        assert '58.278' in normalized,filename
        for old in prohibited:assert old not in normalized,(filename,old)
        assert path.stat().st_size<15*1024*1024,filename
        assert '\ufffd' not in content,filename
        pdfs.append({'file':filename,'pages':len(reader.pages),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    # A repeat build must preserve the manually reviewed test report byte for byte.
    import build_materials
    before=hashlib.sha256((ROOT/'docs/测试与验证报告.md').read_bytes()).hexdigest()
    build_materials.pdf=lambda *args: None  # Verify source handling without rewriting checked artifacts.
    build_materials.build_report(None,None)
    after=hashlib.sha256((ROOT/'docs/测试与验证报告.md').read_bytes()).hexdigest()
    assert before==after, 'Rebuild overwrote the reviewed report'
    result={'version':'2.4.1-slot-aware','date':'2026-11-12','evidence_numbers_checked':True,'obsolete_claims_checked':True,'report_source_preserved_on_rebuild':True,'pdfs':pdfs,'note':'Checks use existing measurement records; no new physical-device benchmark. Visual PNG review is separate.'}
    (ROOT/'results/materials_consistency_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Evidence counts, retest/reference values, current PDFs and source-preserving rebuild passed.')

if __name__=='__main__':main()
