// Opaque-origin host adapter. All PPTX parsing, layout and rendering belongs to the vendor.
import {PptxViewer, parseZipLazyMedia, buildPresentation, buildTextIndex, serializePresentation, RECOMMENDED_ZIP_LIMITS} from '/presentation-vendor.js';

const CHANNEL='pubnexus.presentation.v1',nonce=location.hash.slice(1),viewport=document.getElementById('viewport'),stage=document.getElementById('stage');
let alive=true,initialized=false,revision=null,parentOrigin='*',viewer=null,model=null,handle=null,highlightHandle=null,activeHighlight=null,renderGeneration=0,renderedViewportWidth=0,pageIndex=0,zoom=100,searchEpoch=0,results=[],readySent=false,resizeFrame=0;
const warnings=new Set(),handles=new Map();
// This adapter owns the opaque frame. Record only actual browser-created media URLs,
// without changing the vendor, its Blob, its receiver or its returned URL.
const mediaBlobs=new Map(),createObjectURL=URL.createObjectURL,revokeObjectURL=URL.revokeObjectURL;
function recordObjectURL(...args){const url=Reflect.apply(createObjectURL,this,args),blob=args[0];if(blob instanceof Blob&&blob.size<=64*1024*1024&&mediaBlobs.size<512)mediaBlobs.set(url,{blob,digest:null});return url;}
function forgetObjectURL(...args){mediaBlobs.delete(args[0]);return Reflect.apply(revokeObjectURL,this,args);}
URL.createObjectURL=recordObjectURL;URL.revokeObjectURL=forgetObjectURL;
let imageMappings=[],imageMapEpoch=0,imageTokenSequence=0;
const serializedSlides=new Map();
let slots=[],slideScale=0,slideHeight=0,scrollFrame=0;
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
function clearHighlights(){highlightHandle?.dispose();highlightHandle=null;viewer?.clearSearchHighlights();}
async function imageDigest(image){const record=mediaBlobs.get(image.currentSrc||image.src);if(!record)return null;if(!record.digest)record.digest=record.blob.arrayBuffer().then(bytes=>crypto.subtle.digest('SHA-256',bytes)).then(bytes=>Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join(''));return record.digest;}
function pictureNode(index,mapping){let nodes=serializedSlides.get(index);if(!nodes){nodes=serializePresentation({...model,slides:[model.slides[index]]}).slides[0]?.nodes||[];serializedSlides.set(index,nodes);}for(const id of mapping.group_node_ids){const group=nodes.find(node=>node.id===id&&node.nodeType==='group'&&!node.hidden);if(!group)return null;nodes=group.children||[];}return nodes.find(node=>node.id===mapping.node_id&&node.nodeType==='picture'&&!node.hidden)||null;}
function authoredPictureMatches(image,node,mapping){if(mapping.group_node_ids.length)return false;const parent=image.parentElement;if(!parent)return false;const values=[parent.style.left,parent.style.top,parent.style.width,parent.style.height].map(parseFloat),expected=[node.position.x,node.position.y,node.size.w,node.size.h];if(values.some((value,i)=>!Number.isFinite(value)||Math.abs(value-expected[i])>.02))return false;const actual=new DOMMatrix(getComputedStyle(parent).transform),wanted=new DOMMatrix().rotate(node.rotation||0).scale(node.flipH?-1:1,node.flipV?-1:1);return ['a','b','c','d'].every(key=>Math.abs(actual[key]-wanted[key])<.0001);}
async function bindImages(index,entry){const epoch=imageMapEpoch,currentRevision=revision;if(!imageMappings.length)return {bound:0,unmapped:0};const mappings=imageMappings.filter(item=>item.slide===index),images=[...entry.handle.element.querySelectorAll('img')];for(const image of images){if(!image.dataset.sourceImageToken)image.dataset.sourceImageToken=String(++imageTokenSequence);delete image.dataset.sourceImageId;}const digests=await Promise.all(images.map(imageDigest));if(!alive||revision!==currentRevision||epoch!==imageMapEpoch||handles.get(index)!==entry)throw new Error('superseded');const proposals=[];for(const mapping of mappings){const node=pictureNode(index,mapping);if(!node)continue;let matches=images.filter((image,i)=>digests[i]===mapping.image_sha256);if(matches.length>1||mappings.filter(other=>other.image_sha256===mapping.image_sha256).length>1)matches=matches.filter(image=>authoredPictureMatches(image,node,mapping));if(matches.length===1)proposals.push({mapping,image:matches[0]});}entry.images=new Map();for(const item of proposals){if(proposals.filter(other=>other.image===item.image).length!==1)continue;item.image.dataset.sourceImageId=item.mapping.image_id;entry.images.set(item.mapping.image_id,item);}return {bound:entry.images.size,unmapped:mappings.length-entry.images.size};}
function drawImageHighlight(result,entry){const found=entry.images?.get(result.image_id);if(!found||!found.image.isConnected)return null;const image=found.image,parent=image.parentElement,b=result.image_bbox_px,expected=result.source_image_size_px;if(expected&&(!Array.isArray(expected)||expected[0]!==image.naturalWidth||expected[1]!==image.naturalHeight))return null;const hasBounds=b!==undefined&&b!==null;if(hasBounds&&(!Array.isArray(b)||b.length!==4||!b.every(Number.isFinite)))return null;const bounds=hasBounds?b:[0,0,image.naturalWidth,image.naturalHeight];if(bounds[0]<0||bounds[1]<0||bounds[2]>image.naturalWidth||bounds[3]>image.naturalHeight||bounds[2]<=bounds[0]||bounds[3]<=bounds[1])return null;const overlay=document.createElement('div');overlay.dataset.pptxImageHighlight='true';overlay.dataset.sourceImageId=result.image_id;overlay.dataset.locationKind=hasBounds?'ocr':'image';overlay.style.cssText='position:absolute;pointer-events:none;box-sizing:border-box;border:3px solid #ffd666;background:#ffd66629;z-index:10000;';Object.assign(overlay.style,{left:(image.offsetLeft+image.clientWidth*bounds[0]/image.naturalWidth)+'px',top:(image.offsetTop+image.clientHeight*bounds[1]/image.naturalHeight)+'px',width:(image.clientWidth*(bounds[2]-bounds[0])/image.naturalWidth)+'px',height:(image.clientHeight*(bounds[3]-bounds[1])/image.naturalHeight)+'px'});const left=image.offsetLeft+image.clientWidth*bounds[0]/image.naturalWidth,top=image.offsetTop+image.clientHeight*bounds[1]/image.naturalHeight,right=image.offsetLeft+image.clientWidth*bounds[2]/image.naturalWidth,bottom=image.offsetTop+image.clientHeight*bounds[3]/image.naturalHeight;const style=getComputedStyle(parent);if(['hidden','clip'].includes(style.overflowX)&&(right<=0||left>=parent.clientWidth)||['hidden','clip'].includes(style.overflowY)&&(bottom<=0||top>=parent.clientHeight))return null;parent.append(overlay);const actual=overlay.getBoundingClientRect(),slot=slots[result.slideIndex].getBoundingClientRect();if(actual.right<=slot.left||actual.left>=slot.right||actual.bottom<=slot.top||actual.top>=slot.bottom){overlay.remove();return null;}return {element:overlay,dispose:()=>overlay.remove()};}
// Search coordinates are supplied by the vendor. The host owns the overlay on its external handle.
function drawHighlight(result,current){const b=result.bounds;if(!b||![b.x,b.y,b.w,b.h].every(Number.isFinite))return null;const element=document.createElement('div');element.dataset.pptxSearchHighlight='true';element.style.cssText='position:absolute;pointer-events:none;box-sizing:border-box;border:3px solid #ffd666;border-radius:6px;background:#ffd66629;box-shadow:0 0 0 2px #1127;z-index:10000;';Object.assign(element.style,{left:b.x+'px',top:b.y+'px',width:b.w+'px',height:b.h+'px'});current.element.append(element);return {element,dispose:()=>element.remove()};}
function disposeSlide(index){const entry=handles.get(index);if(!entry)return;if(activeHighlight?.slideIndex===index){clearHighlights();}entry.handle.dispose();slots[index]?.replaceChildren();handles.delete(index);}
function disposeHandle(){clearHighlights();for(const index of [...handles.keys()])disposeSlide(index);handle=null;slots=[];stage.replaceChildren();}
async function decodeResources(element){
  const images=[...element.querySelectorAll('img')];
  await Promise.all(images.map(async image=>{await image.decode();if(!image.complete||!image.naturalWidth)throw new Error('슬라이드 그림을 읽지 못했습니다.');}));
  const backgrounds=new Set();for(const node of [element,...element.querySelectorAll('*')]){for(const match of getComputedStyle(node).backgroundImage.matchAll(/url\(["']?([^"')]+)["']?\)/g))backgrounds.add(match[1]);}
  await Promise.all([...backgrounds].map(async url=>{if(!/^(blob:|data:)/.test(url))throw new Error('외부 배경 그림은 차단되었습니다.');const image=new Image();image.src=url;await image.decode();}));
  await document.fonts.ready;await nextFrame();await nextFrame();
  return {images:images.length,backgrounds:backgrounds.size,fonts:document.fonts.status};
}
function anchorAt(point){
  if(!slots.length)return null;const v=viewport.getBoundingClientRect(),x=point?.x??Math.min(v.width/2,300),y=point?.y??Math.min(v.height/2,220);
  const index=Math.max(0,Math.min(slots.length-1,Math.floor((viewport.scrollTop+y-12)/(slideHeight+12)))),r=slots[index].getBoundingClientRect();
  return {index,x:(v.left+x-r.left)/r.width,y:(v.top+y-r.top)/r.height,screenX:x,screenY:y};
}
function applyLayout(){
  const width=viewport.clientWidth,scale=Math.max(1,width-26)/model.width*zoom/100;
  if(model.width*scale*model.height*scale>64_000_000)throw new Error('확대된 슬라이드가 표시 한도를 넘었습니다.');
  if(slots.length&&scale===slideScale)return;
  for(const index of [...handles.keys()])disposeSlide(index);
  slideScale=scale;slideHeight=model.height*scale;renderedViewportWidth=width;
  if(!slots.length){const fragment=document.createDocumentFragment();slots=model.slides.map((_,index)=>{const slot=document.createElement('div');slot.className='slide-slot';slot.dataset.slideIndex=String(index);slot.setAttribute('aria-label',(index+1)+' 슬라이드');fragment.append(slot);return slot;});stage.append(fragment);}
  stage.style.minWidth=model.width*scale+'px';
  for(const slot of slots){slot.style.width=model.width*scale+'px';slot.style.height=slideHeight+'px';}
}
function mountSlide(index){
  if(handles.has(index))return handles.get(index).ready;
  const current=viewer.renderSlideToContainer(index,slots[index],slideScale);if(!current)throw new Error('슬라이드를 표시하지 못했습니다.');
  const entry={handle:current,ready:null,resources:null,images:new Map()};handles.set(index,entry);
  entry.ready=(async()=>{try{await current.ready;if(!alive||handles.get(index)!==entry)throw new Error('superseded');const resources=await decodeResources(current.element);if(!alive||handles.get(index)!==entry)throw new Error('superseded');entry.resources=resources;return entry;}catch(error){if(!alive||handles.get(index)!==entry)throw new Error('superseded');throw error;}})();
  entry.ready.catch(()=>{});return entry.ready;
}
function visiblePage(){return Math.max(0,Math.min(slots.length-1,Math.floor(Math.max(0,viewport.scrollTop+Math.min(viewport.clientHeight/2,slideHeight/2))/(slideHeight+12))));}
function renderData(index,entry,started){const r=entry.handle.element.getBoundingClientRect();return {render_width:r.width,render_height:r.height,page:index+1,pages:model.slides.length,width:model.width,height:model.height,zoom,position:{vertical:viewport.scrollTop/(slideHeight+12),horizontal:viewport.scrollLeft/(model.width*slideScale)},resources:entry.resources,isolation:{opaque_parent:true,native_bridge_absent:!window.pywebview?.api},elapsed_ms:performance.now()-started,warnings:[...warnings],mounted_slides:handles.size};}
async function reconcile(generation,started=performance.now()){
  checkpoint(generation);if(!slots.length||viewport.clientWidth<=26)return;
  const stride=slideHeight+12,top=viewport.scrollTop,height=viewport.clientHeight,index=visiblePage();
  const first=Math.max(0,Math.floor((top-height*.5)/stride)),last=Math.min(slots.length-1,Math.floor((top+height*1.5)/stride));
  for(const mounted of [...handles.keys()])if(mounted<first||mounted>last)disposeSlide(mounted);
  const main=mountSlide(index);for(let i=first;i<=last;i++)if(i!==index)mountSlide(i).catch(error=>{if(safeError(error)!=='superseded')warnings.add(safeError(error));});
  let entry;try{entry=await main;}catch(error){checkpoint(generation);if(index!==visiblePage())throw new Error('superseded');throw error;}checkpoint(generation);if(handles.get(index)!==entry||index!==visiblePage())throw new Error('superseded');
  const intent=activeHighlight,targetIndex=intent?.slideIndex,targetEntry=handles.get(targetIndex);
  if(intent&&!highlightHandle&&targetEntry){
    await targetEntry.ready;checkpoint(generation);
    if(activeHighlight!==intent||handles.get(targetIndex)!==targetEntry||index!==visiblePage())throw new Error('superseded');
    if(intent.kind==='image')await bindImages(targetIndex,targetEntry);
    checkpoint(generation);
    if(activeHighlight!==intent||handles.get(targetIndex)!==targetEntry||index!==visiblePage())throw new Error('superseded');
    if(!highlightHandle)highlightHandle=intent.kind==='image'?drawImageHighlight(intent,targetEntry):drawHighlight(intent,targetEntry.handle);
  }
  pageIndex=index;handle=entry.handle;
  const data=renderData(index,entry,started);document.getElementById('message').textContent='';if(!readySent){readySent=true;post('ready',data);}post('rendered',data);return data;
}
async function render(index,{point,keepAnchor=false}={}){
  if(!validPage(index))throw new Error('슬라이드 번호가 올바르지 않습니다.');
  const anchor=keepAnchor?anchorAt(point):null,generation=++renderGeneration,started=performance.now();
  await waitForViewport(generation);checkpoint(generation);applyLayout();
  if(anchor){const r=slots[anchor.index].getBoundingClientRect(),v=viewport.getBoundingClientRect();viewport.scrollLeft+=r.left+anchor.x*r.width-v.left-anchor.screenX;viewport.scrollTop+=r.top+anchor.y*r.height-v.top-anchor.screenY;}
  else{viewport.scrollTop=slots[index].offsetTop-12;viewport.scrollLeft=0;}
  return reconcile(generation,started);
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
  // Public external handles allow a continuous list with bounded, caller-owned rendered slides.
  viewer=new PptxViewer(stage,{width:1,fitMode:'none',externalMedia:'block',pdfjs:false,lazySlides:true,lazyMedia:true,zipLimits:RECOMMENDED_ZIP_LIMITS,onNodeError:(_id,error)=>warnings.add(safeError(error))});
  viewer.load(model);return render(0);
}
async function command(message){
  if(!viewer||!model)throw new Error('슬라이드를 여는 중입니다.');
  if(message.type==='go'){activeHighlight=null;clearHighlights();return render(Number(message.page)-1);}
  if(message.type==='bind-images'){if(!Array.isArray(message.images)||message.images.length>10000)throw new Error('그림 연결 정보가 올바르지 않습니다.');imageMapEpoch++;imageMappings=message.images.filter(item=>item&&validPage(item.slide)&&typeof item.image_id==='string'&&item.image_id.length<=256&&typeof item.node_id==='string'&&item.node_id.length<=128&&/^[a-f0-9]{64}$/.test(item.image_sha256)&&Array.isArray(item.group_node_ids)&&item.group_node_ids.length<32&&item.group_node_ids.every(id=>typeof id==='string'&&id.length<=128));const bindings=await Promise.all([...handles].map(async([index,entry])=>{await entry.ready;return bindImages(index,entry);}));return {bound:bindings.reduce((sum,item)=>sum+item.bound,0),unmapped:bindings.reduce((sum,item)=>sum+item.unmapped,0)};}
  if(message.type==='resolve-image'){const token=String(message.token||'');for(const [index,entry]of handles){await bindImages(index,entry);for(const [image_id,item]of entry.images)if(item.image.dataset.sourceImageToken===token)return {matched:true,image_id,slide:index};}return {matched:false};}
  if(message.type==='locate-image'){const mapping=imageMappings.find(item=>item.image_id===message.image_id);if(!mapping)return {matched:false};clearHighlights();activeHighlight={kind:'image',slideIndex:mapping.slide,image_id:mapping.image_id,image_bbox_px:message.image_bbox_px,source_image_size_px:message.source_image_size_px};const rendered=await render(mapping.slide),generation=renderGeneration;checkpoint(generation);if(!highlightHandle)return {...rendered,matched:false};highlightHandle.element.scrollIntoView({block:'center',inline:'center'});return {...rendered,matched:true,image_id:mapping.image_id};}
  if(message.type==='restore-position'){const position=message.position;if(!position||![position.vertical,position.horizontal].every(Number.isFinite))throw new Error('원본 위치가 올바르지 않습니다.');const generation=++renderGeneration;await waitForViewport(generation);checkpoint(generation);applyLayout();viewport.scrollTop=Math.max(0,position.vertical)*(slideHeight+12);viewport.scrollLeft=Math.max(0,position.horizontal)*model.width*slideScale;return reconcile(generation);}
  if(message.type==='zoom'){zoom=Math.max(50,Math.min(250,Number(message.zoom)||100));return render(pageIndex,{point:message.point,keepAnchor:true});}
  if(message.type==='search'){
    const query=String(message.query||'').slice(0,2000);searchEpoch++;activeHighlight=null;clearHighlights();results=query?viewer.searchText(query,{useRegex:false,snippetRadius:100}):[];
    return {search_epoch:searchEpoch,results:results.slice(0,500).map(publicResult),truncated:results.length>500};
  }
  if(message.type==='clear-highlight'){searchEpoch++;results=[];activeHighlight=null;clearHighlights();return {};}
  if(message.type==='highlight'){
    if(message.search_epoch!==undefined&&message.search_epoch!==searchEpoch)throw new Error('검색 결과가 바뀌었습니다.');
    const result=results[Number(message.result)];if(!result)throw new Error('검색 결과를 찾지 못했습니다.');
    clearHighlights();activeHighlight=result;
    const rendered=await render(result.slideIndex);const current=renderGeneration;
    const overlay=highlightHandle;checkpoint(current);
    if(!overlay)throw new Error('검색 위치를 표시하지 못했습니다.');
    overlay.element.scrollIntoView({block:'center',inline:'center'});return {...rendered,result:publicResult(result),search_epoch:searchEpoch};
  }
  if(message.type==='locate'){
    const text=String(message.text||'').trim(),slide=Number(message.slide);
    const matches=text?viewer.searchText(text,{useRegex:false}).filter(r=>r.slideIndex===slide&&r.text.trim()===text):[];
    if(matches.length!==1)return {matched:false,count:matches.length};
    clearHighlights();activeHighlight=matches[0];
    const rendered=await render(slide),generation=renderGeneration;
    const overlay=highlightHandle;checkpoint(generation);
    if(!overlay)throw new Error('문단 위치를 표시하지 못했습니다.');overlay.element.scrollIntoView({block:'center',inline:'center'});
    return {...rendered,matched:true,result:publicResult(matches[0])};
  }
  if(message.type==='theme'){document.body.dataset.theme=message.theme==='dark'?'dark':'light';return {theme:document.body.dataset.theme,paper:getComputedStyle(handle?.element||stage).backgroundColor,filters:[...stage.querySelectorAll('img')].map(image=>getComputedStyle(image).filter)};}
  throw new Error('지원하지 않는 슬라이드 요청입니다.');
}
function destroy(){if(!alive)return;renderGeneration++;imageMapEpoch++;for(const cancel of [...layoutWaiters])cancel();cancelAnimationFrame(resizeFrame);cancelAnimationFrame(scrollFrame);observer.disconnect();disposeHandle();viewer?.destroy();if(URL.createObjectURL===recordObjectURL)URL.createObjectURL=createObjectURL;if(URL.revokeObjectURL===forgetObjectURL)URL.revokeObjectURL=revokeObjectURL;mediaBlobs.clear();serializedSlides.clear();imageMappings=[];viewer=null;model=null;results=[];alive=false;window.removeEventListener('message',receive);}
async function receive(event){
  const message=event.data;if(!alive||event.source!==parent||message?.channel!==CHANNEL||message.nonce!==nonce||!nonce||nonce.length>128)return;
  if(message.type==='context'){if(!initialized&&revision===null&&typeof message.revision==='string'&&message.revision){revision=message.revision;parentOrigin=event.origin==='null'?'*':event.origin;}return;}
  if(message.type!=='init'&&message.revision!==revision)return;
  try{if(message.type==='destroy'){response(message.id);destroy();return;}const result=message.type==='init'?await initialize(message,event):await command(message);if(message.id)response(message.id,result);}
  catch(error){if(!alive)return;if(message.id)post('response',{id:message.id,ok:false,code:safeError(error)==='superseded'?'superseded':'render_error',error:safeError(error)});else if(safeError(error)!=='superseded'){document.getElementById('message').textContent=safeError(error);post('error',{error:safeError(error)});}}
}
window.addEventListener('message',receive);
window.addEventListener('keydown',event=>{const action=event.altKey&&!event.ctrlKey&&event.key==='ArrowLeft'?'back':event.altKey&&!event.ctrlKey&&event.key==='ArrowRight'?'forward':event.key==='Escape'?'escape':event.ctrlKey&&!event.altKey&&!event.shiftKey&&event.key.toLowerCase()==='f'?'find':event.ctrlKey&&!event.altKey&&!event.shiftKey&&event.key.toLowerCase()==='o'?'open':null;if(!action||!alive)return;event.preventDefault();post('shortcut',{action});},true);
window.addEventListener('mouseup',event=>{if(!alive||event.button!==3&&event.button!==4)return;event.preventDefault();post('shortcut',{action:event.button===3?'back':'forward'});},true);
window.addEventListener('auxclick',event=>{if(event.button===3||event.button===4)event.preventDefault();},true);
viewport.addEventListener('wheel',event=>{if(!event.ctrlKey)return;event.preventDefault();if(!viewer||!event.deltaY)return;const r=viewport.getBoundingClientRect();post('wheel',{delta:event.deltaY<0?1:-1,point:{x:event.clientX-r.left,y:event.clientY-r.top},trusted:event.isTrusted,prevented:event.defaultPrevented});},{passive:false});
viewport.addEventListener('scroll',()=>{if(!alive||!viewer||!readySent||scrollFrame||viewport.clientWidth<=26)return;scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;const generation=renderGeneration;reconcile(generation).catch(error=>{if(safeError(error)!=='superseded')post('error',{error:safeError(error)});});});},{passive:true});
viewport.addEventListener('click',event=>{if(event.target.closest('a')){event.preventDefault();event.stopImmediatePropagation();}if(window.getSelection()?.toString())return;const slot=event.target.closest('.slide-slot'),index=Number(slot?.dataset.slideIndex),current=handles.get(index)?.handle;if(!current)return;const r=current.element.getBoundingClientRect(),x=(event.clientX-r.left)/r.width*model.width,y=(event.clientY-r.top)/r.height*model.height;if(x<0||y<0||x>model.width||y>model.height)return;const image=event.target instanceof HTMLImageElement?event.target:null;if(image&&!image.dataset.sourceImageToken)image.dataset.sourceImageToken=String(++imageTokenSequence);const image_point=image&&image.clientWidth&&image.clientHeight?{x:event.offsetX/image.clientWidth*image.naturalWidth,y:event.offsetY/image.clientHeight*image.naturalHeight}:null;const entries=buildTextIndex(model).filter(entry=>entry.slideIndex===index&&x>=entry.bounds.x&&x<=entry.bounds.x+entry.bounds.w&&y>=entry.bounds.y&&y<=entry.bounds.y+entry.bounds.h);post('click',{page:index+1,x,y,image_token:image?.dataset.sourceImageToken,image_point,entries:entries.map(entry=>({text:entry.text,bounds:entry.bounds,nodeId:entry.nodeId}))});},true);
const observer=new ResizeObserver(()=>{if(!viewer||!readySent||resizeFrame||viewport.clientWidth<=26||viewport.clientWidth===renderedViewportWidth)return;resizeFrame=requestAnimationFrame(()=>{resizeFrame=0;if(alive&&viewport.clientWidth>26&&viewport.clientWidth!==renderedViewportWidth)render(pageIndex,{keepAnchor:true}).catch(error=>{if(safeError(error)!=='superseded')post('error',{error:safeError(error)});});});});observer.observe(viewport);
window.addEventListener('pagehide',destroy);
