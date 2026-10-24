async function uiPresentationSuspendRegression(page,baseURL){
  const context=await page.context().browser().newContext(),p=await context.newPage();let release,releaseMetadata,closing=false,holdMetadata=true;
  const gate=new Promise(resolve=>release=resolve);let count=0;
  const metadataGate=new Promise(resolve=>releaseMetadata=resolve);
  try{
    await p.route('**/open?*',async route=>{if(new URL(route.request().url()).searchParams.get('mode')==='result')return route.continue();if(holdMetadata){await metadataGate;holdMetadata=false;}return route.fulfill({json:{source_kind:'presentation',source_ready:true,pages:[[960,720],[960,720],[960,720]],source_revision:'fixture-source-v1',source_data_url:'/presentation-data?p=1&r=fixture-source-v1',source_frame_url:'/presentation-frame',source_unit_label:'슬라이드'}});});
    await p.route('**/presentation-data?*',async route=>{count++;if(count===1)await gate;try{await route.continue();}catch(error){if(!closing&&count>1)throw error;}});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="1"] [data-action=read]').evaluate(n=>n.click());await p.waitForSelector('#document h1');
    await p.evaluate(()=>{window.__suspendHeading=document.querySelector('#document h1');document.querySelector('#search').value='needle';document.querySelector('#result-scroll').scrollTop=600;});
    const metadataStarted=p.waitForRequest(r=>r.url().includes('/open?')&&new URL(r.url()).searchParams.get('mode')==='source');await p.locator('#source-tab').click();await metadataStarted;await p.locator('#content-tab').click();releaseMetadata();await p.waitForTimeout(100);
    const metadataFrames=await p.locator('#presentation-frame').count();if(metadataFrames)throw new Error('UI_PRESENTATION_HIDDEN_METADATA_LIFETIME_RED '+metadataFrames);
    const started=p.waitForRequest(r=>r.url().includes('/presentation-data?'));await p.locator('#source-tab').click();await started;
    await p.locator('#content-tab').click();await p.waitForTimeout(80);
    const suspended=await p.evaluate(()=>({frames:document.querySelectorAll('#presentation-frame').length,sameHeading:window.__suspendHeading===document.querySelector('#document h1'),query:document.querySelector('#search').value,scroll:document.querySelector('#result-scroll').scrollTop,note:document.querySelector('#page-note').textContent,cancels:window.__calls.filter(c=>c.name==='cancel').length}));
    if(suspended.frames||!suspended.sameHeading||suspended.query!=='needle'||suspended.scroll!==600||suspended.note||suspended.cancels)throw new Error('UI_PRESENTATION_HIDDEN_PENDING_LIFETIME_RED '+JSON.stringify(suspended));
    release();await p.locator('#source-tab').click();await p.waitForFunction(()=>document.querySelector('#presentation-frame')?.dataset.ready==='true');
    return{ok:true,metadataFrames,suspended,requests:count};
  }finally{closing=true;releaseMetadata();release();await context.close();}
}
