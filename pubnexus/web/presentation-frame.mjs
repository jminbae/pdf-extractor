// Opaque-origin host adapter. All PPTX parsing, layout and rendering belongs to the vendor.
import {PptxViewer, parseZipLazyMedia, buildPresentation, buildTextIndex, RECOMMENDED_ZIP_LIMITS} from '/presentation-vendor.js';

const CHANNEL='pubnexus.presentation.v1',nonce=location.hash.slice(1),viewport=document.getElementById('viewport'),stage=document.getElementById('stage');
let alive=true,initialized=false,revision=null,parentOrigin='*',viewer=null,model=null,handle=null,highlightHandle=null,activeHighlight=null,renderGeneration=0,renderedViewportWidth=0,pageIndex=0,zoom=100,searchEpoch=0,results=[],readySent=false,resizeFrame=0;
const warnings=new Set();
const layoutWaiters=new Set();
const safeError=e=>String(e?.message||e||'슬라이드를 열지 못했습니다.').slice(0,1200);
const post=(type,data={})=>{if(alive)parent.postMessage({channel:CHANNEL,type,nonce,revision,...data},parentOrigin);};
const response=(id,data={})=>post('response',{id,ok:true,...data});
const nextFrame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
const validPage=n=>Number.isInteger(n)&&n>=0&&n<(model?.slides.length||0);
function checkpoint(generation){if(!alive||generation!==renderGeneration)throw new Error('superseded');}
async function waitForViewport(generation){
  while(viewport.clientWidth<=26){await new Promise((resolve,reject)=>{let timer;const cleanup=()=>{clearTimeout(timer);observer.disconnect();layoutWaiters.delete(cancel);};const cancel=()=>{cleanup();reject(new Error('superseded'));};const observer=new ResizeObserver(()=>{if(!alive||generation!==renderGeneration){cancel();return;}if(viewport.clientWidth>26){cleanup();resolve();}});layoutWaiters.add(cancel);timer=setTimeout(()=>{cleanup();reject(new Error('슬라이드 표시 영역을 준비하지 못했습니다.'));},30000);observer.observe(viewport);});checkpoint(generation);}
}
function publicResult(result){return {...result,bounds:{...result.bounds}};}
function disposeHandle(){viewer?.clearSearchHighlights();highlightHandle=null;handle?.dispose();handle=null;stage.replaceChildren();}
async function decodeResources(element){
  const images=[...element.querySelectorAll('img')];
  await Promise.all(images.map(async image=>{await image.decode();if(!image.complete||!image.naturalWidth)throw new Error('슬라이드 그림을 읽지 못했습니다.');}));
  const backgrounds=new Set();for(const node of [element,...element.querySelectorAll('*')]){for(const match of getComputedStyle(node).backgroundImage.matchAll(/url\(["']?([^"')]+)["']?\)/g))backgrounds.add(match[1]);}
  await Promise.all([...backgrounds].map(async url=>{if(!/^(blob:|data:)/.test(url))throw new Error('외부 배경 그림은 차단되었습니다.');const image=new Image();image.src=url;await image.decode();}));
  await document.fonts.ready;await nextFrame();await nextFrame();
  return {images:images.length,backgrounds:backgrounds.size,fonts:document.fonts.status};
}
function anchorAt(point){if(!handle)return null;const r=handle.element.getBoundingClientRect(),v=viewport.getBoundingClientRect();const x=point?.x??Math.min(v.width/2,300),y=point?.y??Math.min(v.height/2,220);return {x:(v.left+x-r.left)/r.width,y:(v.top+y-r.top)/r.height,screenX:x,screenY:y};}
async function render(index,{point,keepAnchor=false}={}){
  if(!validPage(index))throw new Error('슬라이드 번호가 올바르지 않습니다.');
  const anchor=keepAnchor?anchorAt(point):null,generation=++renderGeneration,started=performance.now();
  await waitForViewport(generation);checkpoint(generation);
  pageIndex=index;disposeHandle();
  renderedViewportWidth=viewport.clientWidth;
  const fit=Math.max(1,renderedViewportWidth-26)/model.width,scale=fit*zoom/100;
  if(model.width*scale*model.height*scale>64_000_000)throw new Error('확대된 슬라이드가 표시 한도를 넘었습니다.');
  const slot=document.createElement('div');slot.className='slide-slot';slot.dataset.slideIndex=String(index);slot.style.width=model.width*scale+'px';slot.style.height=model.height*scale+'px';stage.append(slot);
  const current=viewer.renderSlideToContainer(index,slot,scale);if(!current)throw new Error('슬라이드를 표시하지 못했습니다.');handle=current;
  await current.ready;checkpoint(generation);const resources=await decodeResources(current.element);checkpoint(generation);
  if(activeHighlight?.slideIndex===index){highlightHandle=await viewer.highlightSearchResult(activeHighlight,{scrollIntoView:false});checkpoint(generation);}
  if(anchor){const r=current.element.getBoundingClientRect(),v=viewport.getBoundingClientRect();viewport.scrollLeft+=r.left+anchor.x*r.width-v.left-anchor.screenX;viewport.scrollTop+=r.top+anchor.y*r.height-v.top-anchor.screenY;}else{viewport.scrollTop=0;viewport.scrollLeft=0;}
  const data={render_width:current.element.getBoundingClientRect().width,render_height:current.element.getBoundingClientRect().height,page:index+1,pages:model.slides.length,width:model.width,height:model.height,zoom,resources,isolation:{opaque_parent:true,native_bridge_absent:!window.pywebview?.api},elapsed_ms:performance.now()-started,warnings:[...warnings]};
  document.getElementById('message').textContent='';if(!readySent){readySent=true;post('ready',data);}post('rendered',data);return data;
}
async function initialize(message,event){
  if(initialized||revision!==null&&revision!==message.revision||!message.revision||!(message.buffer instanceof ArrayBuffer)||!message.buffer.byteLength||message.buffer.byteLength>256*1024*1024)throw new Error('슬라이드 입력이 올바르지 않습니다.');
  initialized=true;
  revision=String(message.revision);parentOrigin=event.origin==='null'?'*':event.origin;zoom=Math.max(50,Math.min(250,Number(message.zoom)||100));
  let parentBlocked=false;try{void parent.document.body;}catch{parentBlocked=true;}
  if(!parentBlocked||window.pywebview?.api)throw new Error('슬라이드 보안 격리를 확인하지 못했습니다. PDF 보기로 열어주세요.');
  document.body.dataset.theme=message.theme==='dark'?'dark':'light';
  const generation=renderGeneration,files=await parseZipLazyMedia(message.buffer,RECOMMENDED_ZIP_LIMITS);checkpoint(generation);
  model=buildPresentation(files,{lazySlides:true});
  if(!model.slides.length||model.slides.length>3000||![model.width,model.height].every(n=>Number.isFinite(n)&&n>0&&n<=20000))throw new Error('슬라이드 크기 또는 개수가 표시 한도를 넘었습니다.');
  // width disables the vendor's adaptive list renderer. Only the externally owned handle is mounted.
  viewer=new PptxViewer(stage,{width:1,fitMode:'none',externalMedia:'block',pdfjs:false,lazySlides:true,lazyMedia:true,zipLimits:RECOMMENDED_ZIP_LIMITS,onNodeError:(_id,error)=>warnings.add(safeError(error))});
  viewer.load(model);return render(0);
}
async function command(message){
  if(!viewer||!model)throw new Error('슬라이드를 여는 중입니다.');
  if(message.type==='go'){activeHighlight=null;return render(Number(message.page)-1);}
  if(message.type==='zoom'){zoom=Math.max(50,Math.min(250,Number(message.zoom)||100));return render(pageIndex,{point:message.point,keepAnchor:true});}
  if(message.type==='search'){
    const query=String(message.query||'').slice(0,2000);searchEpoch++;activeHighlight=null;viewer.clearSearchHighlights();results=query?viewer.searchText(query,{useRegex:false,snippetRadius:100}):[];
    return {search_epoch:searchEpoch,results:results.slice(0,500).map(publicResult),truncated:results.length>500};
  }
  if(message.type==='clear-highlight'){searchEpoch++;results=[];activeHighlight=null;viewer.clearSearchHighlights();highlightHandle=null;return {};}
  if(message.type==='highlight'){
    if(message.search_epoch!==undefined&&message.search_epoch!==searchEpoch)throw new Error('검색 결과가 바뀌었습니다.');
    const result=results[Number(message.result)];if(!result)throw new Error('검색 결과를 찾지 못했습니다.');
    activeHighlight=result;
    const rendered=await render(result.slideIndex);const current=renderGeneration;
    const overlay=highlightHandle;checkpoint(current);
    if(!overlay)throw new Error('검색 위치를 표시하지 못했습니다.');
    overlay.element.scrollIntoView({block:'center',inline:'center'});return {...rendered,result:publicResult(result),search_epoch:searchEpoch};
  }
  if(message.type==='locate'){
    const text=String(message.text||'').trim(),slide=Number(message.slide);
    const matches=text?viewer.searchText(text,{useRegex:false}).filter(r=>r.slideIndex===slide&&r.text.trim()===text):[];
    if(matches.length!==1)return {matched:false,count:matches.length};
    activeHighlight=matches[0];
    const rendered=await render(slide),generation=renderGeneration;
    const overlay=highlightHandle;checkpoint(generation);
    if(!overlay)throw new Error('문단 위치를 표시하지 못했습니다.');overlay.element.scrollIntoView({block:'center',inline:'center'});
    return {...rendered,matched:true,result:publicResult(matches[0])};
  }
  if(message.type==='theme'){document.body.dataset.theme=message.theme==='dark'?'dark':'light';return {theme:document.body.dataset.theme,paper:getComputedStyle(handle?.element||stage).backgroundColor,filters:[...stage.querySelectorAll('img')].map(image=>getComputedStyle(image).filter)};}
  throw new Error('지원하지 않는 슬라이드 요청입니다.');
}
function destroy(){if(!alive)return;renderGeneration++;for(const cancel of [...layoutWaiters])cancel();cancelAnimationFrame(resizeFrame);observer.disconnect();disposeHandle();viewer?.destroy();viewer=null;model=null;results=[];alive=false;window.removeEventListener('message',receive);}
async function receive(event){
  const message=event.data;if(!alive||event.source!==parent||message?.channel!==CHANNEL||message.nonce!==nonce||!nonce||nonce.length>128)return;
  if(message.type==='context'){if(!initialized&&revision===null&&typeof message.revision==='string'&&message.revision){revision=message.revision;parentOrigin=event.origin==='null'?'*':event.origin;}return;}
  if(message.type!=='init'&&message.revision!==revision)return;
  try{if(message.type==='destroy'){response(message.id);destroy();return;}const result=message.type==='init'?await initialize(message,event):await command(message);if(message.id)response(message.id,result);}
  catch(error){if(!alive)return;if(message.id)post('response',{id:message.id,ok:false,code:safeError(error)==='superseded'?'superseded':'render_error',error:safeError(error)});else if(safeError(error)!=='superseded'){document.getElementById('message').textContent=safeError(error);post('error',{error:safeError(error)});}}
}
window.addEventListener('message',receive);
window.addEventListener('keydown',event=>{const action=event.key==='Escape'?'escape':event.ctrlKey&&!event.altKey&&!event.shiftKey&&event.key.toLowerCase()==='f'?'find':null;if(!action||!alive)return;event.preventDefault();post('shortcut',{action});},true);
viewport.addEventListener('wheel',event=>{if(!event.ctrlKey)return;event.preventDefault();if(!viewer||!event.deltaY)return;const r=viewport.getBoundingClientRect();post('wheel',{delta:event.deltaY<0?1:-1,point:{x:event.clientX-r.left,y:event.clientY-r.top},trusted:event.isTrusted,prevented:event.defaultPrevented});},{passive:false});
viewport.addEventListener('click',event=>{if(event.target.closest('a')){event.preventDefault();event.stopImmediatePropagation();}if(!handle||window.getSelection()?.toString())return;const r=handle.element.getBoundingClientRect();const x=(event.clientX-r.left)/r.width*model.width,y=(event.clientY-r.top)/r.height*model.height;if(x<0||y<0||x>model.width||y>model.height)return;const entries=buildTextIndex(model).filter(entry=>entry.slideIndex===pageIndex&&x>=entry.bounds.x&&x<=entry.bounds.x+entry.bounds.w&&y>=entry.bounds.y&&y<=entry.bounds.y+entry.bounds.h);post('click',{page:pageIndex+1,x,y,entries:entries.map(entry=>({text:entry.text,bounds:entry.bounds,nodeId:entry.nodeId}))});},true);
const observer=new ResizeObserver(()=>{if(!viewer||!readySent||resizeFrame||viewport.clientWidth<=26||viewport.clientWidth===renderedViewportWidth)return;resizeFrame=requestAnimationFrame(()=>{resizeFrame=0;if(alive&&viewport.clientWidth>26&&viewport.clientWidth!==renderedViewportWidth)render(pageIndex,{keepAnchor:true}).catch(error=>{if(safeError(error)!=='superseded')post('error',{error:safeError(error)});});});});observer.observe(viewport);
window.addEventListener('pagehide',destroy);
