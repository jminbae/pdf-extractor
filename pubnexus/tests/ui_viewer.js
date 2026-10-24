async function uiViewerRegression(page, baseURL) {
  // Real viewer JS against the private synthetic HTTP/native fixture only.
  const context=await page.context().browser().newContext({viewport:{width:1336,height:900}});
  const p=await context.newPage();
  let releasePreview,releaseResult,closing=false;
  try {
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    await p.locator('.file-row[data-index="1"]').dblclick();
    await p.waitForSelector('#source-scroll .pdf-page');
    await p.locator('#source-scroll .pdf-page img').first().evaluate(n=>n.decode());
    const pane=await p.locator('#source-scroll').boundingBox();
    const x=pane.x+pane.width/2,y=pane.y+150;
    const before=await p.evaluate(() => ({width:document.querySelector('.pdf-page').getBoundingClientRect().width,
      toolbar:document.querySelector('.toolbar').getBoundingClientRect().height,
      text:document.querySelector('#source-zoom-reset')?.textContent,
      scroll:document.querySelector('#source-scroll').scrollTop}));
    await p.evaluate(() => {window.__viewerPage=document.querySelector('.pdf-page');});
    await p.mouse.move(x,y);await p.keyboard.down('Control');
    try {await p.mouse.wheel(0,-100);} finally {await p.keyboard.up('Control');}
    await p.waitForTimeout(100);
    const after=await p.evaluate(() => ({width:document.querySelector('.pdf-page').getBoundingClientRect().width,
      toolbar:document.querySelector('.toolbar').getBoundingClientRect().height,
      text:document.querySelector('#source-zoom-reset')?.textContent,
      samePage:window.__viewerPage===document.querySelector('.pdf-page'),
      filter:getComputedStyle(document.querySelector('.pdf-page img')).filter}));
    if(before.text?.trim()!=='100%'||after.text?.trim()!=='110%'||Math.abs(after.width/before.width-1.1)>.02)
      throw new Error('UI_VIEWER_SOURCE_ZOOM_RED '+JSON.stringify({before,after}));
    if(!after.samePage||Math.abs(after.toolbar-before.toolbar)>1||after.filter!=='none')
      throw new Error('UI_VIEWER_SOURCE_ZOOM_SCOPE_RED '+JSON.stringify({before,after}));
    await p.locator('#source-zoom-reset').click();
    const reset=await p.evaluate(() => ({percent:document.querySelector('#source-zoom-reset').textContent,
      width:document.querySelector('.pdf-page').getBoundingClientRect().width}));
    if(reset.percent.trim()!=='100%'||Math.abs(reset.width-before.width)>1)
      throw new Error('UI_VIEWER_SOURCE_ZOOM_RESET_RED '+JSON.stringify(reset));
    await p.locator('#reader-close').click();
    const gate=new Promise(resolve=>{releasePreview=resolve;});
    const resultGate=new Promise(resolve=>{releaseResult=resolve;});
    const requests=[];
    await p.route('**/open?*',async route=>{try{const url=new URL(route.request().url());requests.push(url.searchParams.get('mode'));
      if(url.searchParams.get('mode')!=='result')await gate;else await resultGate;
      if(url.searchParams.get('mode')==='source'){
        const response=await route.fetch(),payload=await response.json();
        await route.fulfill({response,json:{...payload,pages:Array.from({length:71},()=>[400,500]),
          source_unit_label:'슬라이드',source_label:'원본 슬라이드'}});
      }else await route.continue();}catch(error){if(!closing)throw error;}});
    await p.route('**/document-map?*',async route=>{try{await gate;await route.continue();}catch(error){if(!closing)throw error;}});
    await p.evaluate(()=>window.pnx.on({kind:'folder',...window.__fixture,files:window.__fixture.files.map((f,i)=>({...f,...(i===1?{needs_refresh:true}:{})}))}));
    const firstResult=p.waitForRequest(r=>new URL(r.url()).searchParams.get('mode')==='result');
    await p.locator('.file-row[data-index="1"] [data-action=read]').evaluate(n=>n.click());
    await firstResult;await p.locator('#code-tab').click();await p.waitForTimeout(80);
    if(requests.filter(mode=>mode==='result').length!==1)
      throw new Error('UI_VIEWER_DUPLICATE_RESULT_RED '+JSON.stringify({requests}));
    releaseResult();await p.waitForSelector('#document h1',{state:'attached'});await p.locator('#content-tab').click();
    try {await p.waitForSelector('#document h1',{timeout:1500});}
    catch {throw new Error('UI_VIEWER_MD_WAITS_FOR_PREVIEW_RED '+JSON.stringify({requests}));}
    const resultOnly={requests:[...requests],heading:await p.locator('#document h1').textContent()};
    if(requests.some(mode=>mode!=='result'))throw new Error('UI_VIEWER_EAGER_PREVIEW_RED '+JSON.stringify(resultOnly));
    await p.locator('#reader-close').click();
    await p.locator('.file-row[data-index="1"]').dblclick();
    await p.waitForSelector('.source-loading');
    if(await p.locator('#reader-back').count()!==1||await p.locator('#reader-extract').count()!==1)
      throw new Error('UI_VIEWER_ACTIONS_RED missing explicit list/current extraction during preview');
    const preparing=await p.evaluate(() => ({back:document.querySelector('#reader-back').textContent,
      extractDisabled:document.querySelector('#reader-extract').disabled,
      preparing:document.querySelector('.source-loading').textContent}));
    if(preparing.extractDisabled||!preparing.back.includes('목록'))throw new Error('UI_VIEWER_PREVIEW_BLOCKS_ACTIONS_RED '+JSON.stringify(preparing));
    await p.locator('#reader-extract').click();
    const extraction=await p.evaluate(() => window.__calls.filter(c=>c.name==='extract_selected').at(-1));
    if(JSON.stringify(extraction?.args)!==JSON.stringify([[1],true,[await p.evaluate(()=>window.__fixture.files[1].path)]]))throw new Error('UI_VIEWER_WRONG_DOCUMENT_RED '+JSON.stringify(extraction));
    await p.evaluate(()=>{window.pnx.on({kind:'run',on:true});window.pnx.on({kind:'prog',path:window.__fixture.files[1].path,
      name:'B.pdf',file_pct:37,pct:12,stage:'본문 추출'});});
    const running=await p.locator('#reader-extract').evaluate(n=>({disabled:n.disabled,text:n.textContent}));
    if(!running.disabled||!running.text.includes('37%'))throw new Error('UI_VIEWER_JOB_STATUS_RED '+JSON.stringify(running));
    releasePreview();
    await p.waitForFunction(()=>document.querySelector('#page-total').textContent.includes('71'));
    await p.locator('#source-position').click();await p.locator('#page-input').fill('6');await p.locator('#page-input').press('Enter');
    const slide=await p.evaluate(()=>({label:document.querySelector('#source-label').textContent,
      page:document.querySelector('#page-input').value,total:document.querySelector('#page-total').textContent,
      accessible:document.querySelector('#page-input').getAttribute('aria-label')}));
    if(slide.label!=='원본'||slide.page!=='6'||slide.total.trim()!=='/ 71'||!slide.accessible.includes('슬라이드'))
      throw new Error('UI_VIEWER_SLIDE_POSITION_RED '+JSON.stringify(slide));
    await p.locator('#reader-back').click();
    const back=await p.evaluate(()=>({list:!document.querySelector('#list-view').hidden,
      cancels:window.__calls.filter(c=>c.name==='cancel').length,stopEnabled:!document.querySelector('#stop').disabled}));
    if(!back.list||back.cancels||!back.stopEnabled)throw new Error('UI_VIEWER_BACK_CANCELLED_EXTRACTION_RED '+JSON.stringify(back));
    return {ok:true,frontendFixtureOnly:true,proof:{before,after,reset,resultOnly,preparing,extraction,running,slide,back}};
  } finally {closing=true;releasePreview?.();releaseResult?.();await context.close();}
}
