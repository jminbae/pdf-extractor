async function uiPresentationLayoutRegression(page,baseURL){
  const context=await page.context().browser().newContext(),p=await context.newPage();
  try{
    await p.goto(baseURL+'presentation-test-host');
    await p.evaluate(()=>{const host=document.querySelector('#host');host.style.width='0px';const frame=document.createElement('iframe');frame.id='layout-frame';frame.sandbox='allow-scripts';frame.style='width:100%;height:100%;border:0';frame.src='/presentation-frame#layout-nonce-8364';window.__layoutPackets=[];window.addEventListener('message',e=>{if(e.source===frame.contentWindow)window.__layoutPackets.push(e.data);});frame.onload=async()=>{const buffer=await(await fetch('/presentation-data?p=1&r=fixture-source-v1')).arrayBuffer();frame.contentWindow.postMessage({channel:'pubnexus.presentation.v1',type:'init',nonce:'layout-nonce-8364',revision:'fixture-source-v1',buffer},'*',[buffer]);window.__layoutTransferred=true;};host.append(frame);});
    await p.waitForFunction(()=>window.__layoutTransferred);await p.waitForTimeout(150);
    const premature=await p.evaluate(()=>window.__layoutPackets.filter(m=>m.type==='ready'));
    if(premature.length)throw new Error('UI_PRESENTATION_PREMATURE_LAYOUT_READY_RED '+JSON.stringify(premature));
    await p.locator('#host').evaluate(n=>n.style.width='1000px');await p.waitForFunction(()=>window.__layoutPackets.some(m=>m.type==='ready'));
    const ready=await p.evaluate(()=>window.__layoutPackets.find(m=>m.type==='ready'));
    if(!Number.isFinite(ready.render_width)||ready.render_width<500||ready.resources.images!==1)throw new Error('UI_PRESENTATION_INVISIBLE_READY_RED '+JSON.stringify(ready));
    return{ok:true,premature,ready};
  }finally{await context.close();}
}
