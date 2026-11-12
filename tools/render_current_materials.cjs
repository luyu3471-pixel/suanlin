// After python tools/build_materials.py:
// node tools/render_current_materials.cjs
// Needs Playwright (npm); NODE_PATH may point at the bundled Node packages.
// Exports the reviewed deck and records a report walkthrough, not a live experiment.
const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const ROOT = path.resolve(__dirname, '..');
async function checkVideo(browser,out) {
  const page=await browser.newPage({viewport:{width:1280,height:720}});
  const file=path.join(ROOT,'delivery','04-演示视频.mp4');
  await page.goto(pathToFileURL(file).href);
  const metadata=await page.evaluate(async()=>{
    const v=document.querySelector('video');
    if(v.readyState<1)await new Promise(resolve=>v.addEventListener('loadedmetadata',resolve,{once:true}));
    v.pause();v.controls=false;v.style.cssText='position:fixed;inset:0;width:1280px;height:720px';
    return {duration:v.duration,width:v.videoWidth,height:v.videoHeight};
  });
  if(metadata.width!==1280||metadata.height!==720||metadata.duration<180||metadata.duration>300)throw new Error(JSON.stringify(metadata));
  for(const second of [1,120,190]) {
    await page.evaluate(async second=>{
      const v=document.querySelector('video');
      await new Promise((resolve,reject)=>{v.addEventListener('seeked',resolve,{once:true});v.addEventListener('error',reject,{once:true});v.currentTime=second;});
    },second);
    await page.screenshot({path:path.join(out,`video-${second}.png`)});
  }
  const result={...metadata,sampled_seconds:[1,120,190],sha256:require('crypto').createHash('sha256').update(fs.readFileSync(file)).digest('hex'),decoder:'system Chrome',note:'Existing report walkthrough; no new dual-machine test'};
  fs.writeFileSync(path.join(ROOT,'results','materials_video_checks.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result));
  await page.close();
}
async function main() {
  const out = path.join(ROOT,'tmp','materials-review');
  fs.mkdirSync(out,{recursive:true});
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    if(process.argv.includes('--check-video')) {await checkVideo(browser,out);return;}
    const page = await browser.newPage({viewport:{width:1280,height:720},deviceScaleFactor:1});
    await page.goto(pathToFileURL(path.join(ROOT,'delivery','02-答辩展示.html')).href);
    await page.addStyleTag({content:'@media screen{.slide{margin:0;box-shadow:none}body{background:#f8faf5}}html{scroll-behavior:auto}'});
    await page.evaluate(()=>document.fonts.ready);
    const issues = await page.evaluate(()=>[...document.querySelectorAll('.slide')].flatMap((s,i)=>{
      const base=s.getBoundingClientRect(), body=s.querySelector('.body').getBoundingClientRect(),foot=s.querySelector('footer').getBoundingClientRect();
      return body.bottom>foot.top-8 ? [{slide:i+1,bodyBottom:body.bottom-base.top,footerTop:foot.top-base.top}] : [];
    }));
    if(issues.length) throw new Error(JSON.stringify(issues));
    await page.pdf({path:path.join(ROOT,'delivery','02-答辩展示.pdf'),printBackground:true,preferCSSPageSize:true});
    const slideImages=[];
    const count=await page.locator('.slide').count();
    for(let i=0;i<count;i++) {
      const buffer=await page.locator('.slide').nth(i).screenshot({path:path.join(out,`slide-${String(i+1).padStart(2,'0')}.png`)});
      slideImages.push(buffer.toString('base64'));
    }
    const captions=[
      '2.4.1报告讲解｜展示已有证据，非新录现场双机操作；无旁白。',
      '办公需求为待验证假设，尚无实际用户访谈或业务试点。',
      'Qwen仅在A调度；A/B实际执行日志、检索、脱敏压缩CPU算法。',
      '截图采集于2026-11-11；已验证A四槽/B四槽，不代表当前实时连接。',
      '原样模型、排程保护和规则回退分别记录；模型等待计入总时延。',
      '租约可重排，可能重复计算；可信范围不等于硬件证明或加密。',
      '历史仿真不含Qwen，网络参数是假设，不能作为双机或6G成绩。',
      '450项为跨版本总量。最终AI专项58.278秒，前轮规则51.559秒更快。',
      '运行时AI是A上的Qwen；旧轻量分类保留演示，尚无6G无线测试。',
      '压缩跨机差异未解，整体正确性未验收；继续需求与业务质量验证。',
      '11月12日只更新材料，未新增双机实测；两名成员仍须审核。',
      'Qwen/llama.cpp为第三方资源；本地量化来源、哈希和构建号待补。'
    ];
    // Chrome's MediaRecorder uses the installed system H.264 encoder and MP4 muxer.
    const support=await page.evaluate(()=>MediaRecorder.isTypeSupported('video/mp4;codecs=avc1.42001E'));
    if(!support) throw new Error('System Chrome does not support H.264/MP4 recording');
    console.log('PDF and 12 slide PNGs exported; recording 192-second evidence walkthrough.');
    const data=await page.evaluate(async ({slides,captions})=>{
      const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=720;
      const ctx=canvas.getContext('2d');
      const images=await Promise.all(slides.map(src=>new Promise(resolve=>{const img=new Image();img.onload=()=>resolve(img);img.src='data:image/png;base64,'+src;})));
      const chunks=[],stream=canvas.captureStream(10);
      const recorder=new MediaRecorder(stream,{mimeType:'video/mp4;codecs=avc1.42001E',videoBitsPerSecond:1500000});
      recorder.ondataavailable=e=>chunks.push(e.data);
      const done=new Promise(resolve=>recorder.onstop=resolve);
      let scene=0;
      function draw(){
        ctx.drawImage(images[scene],0,0);
        ctx.fillStyle='#183f35';ctx.fillRect(0,668,1280,52);
        ctx.font='20px "Microsoft YaHei",sans-serif';ctx.fillStyle='#f2f7e8';ctx.textAlign='center';ctx.fillText(captions[scene],640,701,1220);
      }
      draw();recorder.start(1000);
      const refresh=setInterval(draw,100);
      for(let i=0;i<images.length;i++){scene=i;draw();await new Promise(resolve=>setTimeout(resolve,16000));}
      clearInterval(refresh);recorder.stop();await done;stream.getTracks().forEach(t=>t.stop());
      const blob=new Blob(chunks,{type:'video/mp4'});
      return await new Promise(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(blob);});
    },{slides:slideImages,captions});
    const file=path.join(ROOT,'delivery','04-演示视频.mp4');fs.writeFileSync(file,Buffer.from(data,'base64'));
    const videoPage=await browser.newPage();
    await videoPage.goto(pathToFileURL(file).href);
    const duration=await videoPage.evaluate(async()=>{const v=document.querySelector('video');if(v.readyState<1)await new Promise(resolve=>v.addEventListener('loadedmetadata',resolve,{once:true}));return v.duration;});
    if(!Number.isFinite(duration)||duration<180||duration>300) throw new Error('Invalid recorded duration: '+duration);
    const result={version:'2.4.1-slot-aware',material_date:'2026-11-12',measurement_date:'2026-11-11',file:path.basename(file),bytes:fs.statSync(file).size,duration_seconds:duration,width:1280,height:720,video_codec:'H.264',captioned:true,audio:false,source:'192-second walkthrough of updated slides and existing evidence; not new live dual-machine recording',scenes:captions.map((caption,i)=>({at:i*16,seconds:16,caption}))};
    fs.writeFileSync(path.join(ROOT,'results','video_record.json'),JSON.stringify(result,null,2)+'\n');
    fs.writeFileSync(path.join(ROOT,'results','materials_slide_checks.json'),JSON.stringify({slide_count:count,layout_issues:issues,recording:result.source,playwright:require('playwright/package.json').version},null,2)+'\n');
    await checkVideo(browser,out);
    console.log(JSON.stringify({slide_count:count,layout_issues:issues,duration_seconds:duration,video_bytes:result.bytes}));
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
