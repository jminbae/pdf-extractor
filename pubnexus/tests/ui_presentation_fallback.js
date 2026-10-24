async function uiPresentationFallbackRegression(page,baseURL){
  const context=await page.context().browser().newContext(),p=await context.newPage();let releaseData,releasePdf,closing=false,dataRequests=0;
  const dataGate=new Promise(resolve=>releaseData=resolve),pdfGate=new Promise(resolve=>releasePdf=resolve);
  try{
    await p.route('**/presentation-data?*',async route=>{dataRequests++;await dataGate;try{await route.continue();}catch(error){if(!closing&&dataRequests>1)throw error;}});
    await p.route('**/open?*',async route=>{const u=new URL(route.request().url());if(u.searchParams.get('renderer')==='pdf'){await pdfGate;return route.fulfill({json:{source_kind:'pdf',source_ready:true,key:1,pages:[[400,500]],source_unit_label:'쪽',source_label:'원본 PDF'}});}
      return route.fulfill({json:{source_kind:'presentation',source_ready:true,pages:[[960,720],[960,720],[960,720]],source_revision:'fixture-source-v1',source_data_url:'/presentation-data?p=1&r=fixture-source-v1',source_frame_url:'/presentation-frame',source_unit_label:'슬라이드'}});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');const dataStarted=p.waitForRequest(r=>r.url().includes('/presentation-data?'));await p.locator('.file-row[data-index="1"]').dblclick();await dataStarted;
    await p.waitForFunction(()=>document.querySelector('#source-pdf-fallback')&&!document.querySelector('#source-pdf-fallback').hidden);
    await p.locator('#source-pdf-fallback').click();await p.waitForTimeout(100);
    const during={frames:await p.locator('#presentation-frame').count(),dataRequests,note:await p.locator('#page-note').textContent()};
    if(during.frames||during.dataRequests!==1)throw new Error('UI_PRESENTATION_FALLBACK_RESTARTS_OLD_FRAME_RED '+JSON.stringify(during));
    releasePdf();await p.waitForSelector('.pdf-page');releaseData();await p.waitForTimeout(100);
    const final={frames:await p.locator('#presentation-frame').count(),pdfPages:await p.locator('.pdf-page').count(),note:await p.locator('#page-note').textContent()};
    if(final.frames||final.pdfPages!==1||final.note)throw new Error('UI_PRESENTATION_FALLBACK_STALE_RED '+JSON.stringify(final));
    return{ok:true,during,final};
  }finally{closing=true;releaseData();releasePdf();await context.close();}
}
