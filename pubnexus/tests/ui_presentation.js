async function uiPresentationRegression(page, baseURL) {
  // Public postMessage interface of the real sandboxed renderer, generated PPTX only.
  const context=await page.context().browser().newContext({viewport:{width:1336,height:900}});
  const p=await context.newPage(),requests=[];p.on('request',r=>requests.push(r.url()));
  try {
    await p.goto(baseURL+'presentation-test-host');
    await p.evaluate(async()=>{
      window.__nativeCalls=0;window.pywebview={api:{sentinel:()=>{window.__nativeCalls++;}}};
      window.__packets=[];window.__nonce='fixture-frame-nonce-7391';window.__revision='fixture-source-v1';
      const frame=document.createElement('iframe');frame.id='frame';frame.sandbox='allow-scripts';
      frame.style='width:100%;height:100%;border:0';frame.src='/presentation-frame#'+window.__nonce;
      window.addEventListener('message',event=>{if(event.source===frame.contentWindow)window.__packets.push(event.data);});
      frame.addEventListener('load',async()=>{const response=await fetch('/presentation-data?p=1&r='+window.__revision);const buffer=await response.arrayBuffer();
        frame.contentWindow.postMessage({channel:'pubnexus.presentation.v1',type:'init',nonce:window.__nonce,revision:window.__revision,buffer,zoom:100},'*',[buffer]);
        window.__transferred=buffer.byteLength===0;});
      document.querySelector('#host').append(frame);
      window.__send=(type,payload={},id=String(Math.random()))=>{frame.contentWindow.postMessage({channel:'pubnexus.presentation.v1',type,nonce:window.__nonce,revision:window.__revision,id,...payload},'*');return id;};
    });
    try {await p.waitForFunction(()=>window.__packets.some(m=>m.type==='ready'),null,{timeout:2500});}
    catch {throw new Error('UI_PRESENTATION_READY_RED '+JSON.stringify(await p.evaluate(()=>window.__packets)));}
    const frame=p.frames().find(f=>f.url().includes('/presentation-frame#'));
    const initial=await frame.evaluate(()=>({text:document.body.innerText,images:[...document.images].map(i=>({complete:i.complete,width:i.naturalWidth})),
      isolated:(()=>{try{return !parent.pywebview;}catch{return true;}})(),fonts:document.fonts.status}));
    const packets=await p.evaluate(()=>({ready:window.__packets.find(m=>m.type==='ready'),transferred:window.__transferred,nativeCalls:window.__nativeCalls}));
    if(!initial.text.includes('FAST_FRAME_FIRST_7391')||!initial.images.length||initial.images.some(i=>!i.complete||!i.width)||
      !initial.isolated||initial.fonts!=='loaded'||!packets.transferred||packets.nativeCalls)
      throw new Error('UI_PRESENTATION_INCOMPLETE_OR_UNISOLATED_RED '+JSON.stringify({initial,packets}));
    await p.evaluate(()=>{const f=document.querySelector('#frame');for(const bad of [{nonce:'wrong'},{revision:'wrong'}])f.contentWindow.postMessage({channel:'pubnexus.presentation.v1',type:'go',nonce:window.__nonce,revision:window.__revision,id:'forged',page:3,...bad},'*');});
    await p.waitForTimeout(50);
    if(!(await frame.locator('body').innerText()).includes('FAST_FRAME_FIRST_7391')||await p.evaluate(()=>window.__packets.some(m=>m.id==='forged')))
      throw new Error('UI_PRESENTATION_UNTRUSTED_COMMAND_RED');
    await p.evaluate(()=>window.__send('go',{page:2},'go-two'));
    await p.waitForFunction(()=>window.__packets.some(m=>m.type==='response'&&m.id==='go-two'&&m.ok));
    if(!(await frame.locator('body').innerText()).includes('FAST_FRAME_SECOND_7391'))throw new Error('UI_PRESENTATION_NAV_RED');
    await p.evaluate(()=>window.__send('search',{query:'THIRD'},'search-third'));
    await p.waitForFunction(()=>window.__packets.some(m=>m.type==='response'&&m.id==='search-third'));
    const found=await p.evaluate(()=>window.__packets.find(m=>m.id==='search-third'));
    if(!found.results?.length||found.results[0].slideIndex!==2)throw new Error('UI_PRESENTATION_SEARCH_RED '+JSON.stringify(found));
    await p.evaluate(()=>window.__send('highlight',{result:0,search_epoch:window.__packets.find(m=>m.id==='search-third').search_epoch},'highlight-third'));
    await p.waitForFunction(()=>window.__packets.some(m=>m.type==='response'&&m.id==='highlight-third'&&m.ok));
    const highlighted=await frame.locator('[data-pptx-search-highlight]').count();
    if(!highlighted||!(await frame.locator('body').innerText()).includes('FAST_FRAME_THIRD_7391'))throw new Error('UI_PRESENTATION_HIGHLIGHT_RED');
    await p.evaluate(()=>{window.__send('go',{page:1},'burst-first');window.__send('go',{page:2},'burst-second');});
    await p.waitForFunction(()=>window.__packets.some(m=>m.id==='burst-second'&&m.ok));
    if(!(await frame.locator('body').innerText()).includes('FAST_FRAME_SECOND_7391'))throw new Error('UI_PRESENTATION_NAV_ORDER_RED');
    await p.evaluate(()=>{window.__send('search',{query:'FIRST'},'new-search');window.__send('highlight',{result:0,search_epoch:1},'old-highlight');});
    await p.waitForFunction(()=>window.__packets.some(m=>m.id==='old-highlight'));
    if(await p.evaluate(()=>window.__packets.find(m=>m.id==='old-highlight').ok))throw new Error('UI_PRESENTATION_STALE_SEARCH_RED');
    const denied=await frame.evaluate(()=>{let dom=false,bridge=false;try{parent.document.querySelector('body');}catch{dom=true;}try{parent.pywebview.api.sentinel();}catch{bridge=true;}return {dom,bridge};});
    if(!denied.dom||!denied.bridge)throw new Error('UI_PRESENTATION_PARENT_ACCESS_RED '+JSON.stringify(denied));
    await p.evaluate(()=>window.__send('destroy',{},'destroy'));
    await p.waitForFunction(()=>window.__packets.some(m=>m.type==='response'&&m.id==='destroy'&&m.ok));
    const destroyed=await frame.locator('#stage').evaluate(n=>n.childElementCount===0);
    if(!destroyed)throw new Error('UI_PRESENTATION_DESTROY_RED');
    const external=requests.filter(url=>!url.startsWith(baseURL)&&!url.startsWith('blob:')&&!url.startsWith('data:'));
    if(external.length)throw new Error('UI_PRESENTATION_EXTERNAL_NETWORK_RED '+JSON.stringify(external));
    return {ok:true,generatedPptxOnly:true,initial,packets,found,highlighted,denied,destroyed,requests};
  } finally {await context.close();}
}
