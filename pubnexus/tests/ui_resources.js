async function uiResourcesRegression(page, baseURL) {
  const context = await page.context().browser().newContext({viewport:{width:1336,height:900}});
  const p = await context.newPage();
  const errors=[];p.on('pageerror',error=>errors.push(String(error)));
  try {
    await p.goto(baseURL);
    await p.waitForSelector('.file-row');
    await p.locator('.file-row[data-index="1"] [data-action=read]').click();
    await p.waitForSelector('#document h1');
    await p.locator('#search').fill('needle');
    await p.locator('#search-form button[type=submit]').click();
    await p.waitForSelector('.search-hit');
    await p.evaluate(() => {
      document.querySelector('#result-scroll').scrollTop=700;
      window.pnx.on({kind:'run',on:true});
      window.pnx.on({kind:'prog',path:window.__fixture.files[0].path,name:'A.pdf',file_pct:4,pct:4,stage:'문서 구조 추출'});
      window.__rowA=document.querySelector('.file-row[data-index="0"]');
      window.__rowC=document.querySelector('.file-row[data-index="2"]');
      window.pnx.on({kind:'prog',path:window.__fixture.files[2].path,name:'C.pdf',file_pct:37,pct:9,stage:'문서 구조 추출'});
      window.pnx.on({kind:'prog',path:window.__fixture.files[0].path,name:'A.pdf',file_pct:9,pct:12,stage:'문서 구조 추출'});
      window.pnx.on({kind:'resources',running:2,queued:12,total:14,max_workers:8,
        rss_bytes:3*1024**3,private_bytes:4*1024**3,memory_budget_bytes:64*1024**3,
        cpu_percent:16,system_cpu_percent:47,waiting_reason:'메모리 여유를 기다립니다.'});
    });
    const desktop = await p.evaluate(() => ({
      sameA:window.__rowA===document.querySelector('.file-row[data-index="0"]'),
      sameC:window.__rowC===document.querySelector('.file-row[data-index="2"]'),
      running:document.querySelectorAll('.file-row[data-status="running"]').length,
      a:document.querySelector('.file-row[data-index="0"] .status-text').textContent,
      c:document.querySelector('.file-row[data-index="2"] .status-text').textContent,
      resourceVisible:!!document.querySelector('#resources')?.getClientRects().length,
      query:document.querySelector('#search').value,scroll:document.querySelector('#result-scroll').scrollTop,
      width:document.documentElement.scrollWidth,viewport:document.documentElement.clientWidth}));
    await p.setViewportSize({width:375,height:812});
    const mobile = await p.evaluate(() => ({width:document.documentElement.scrollWidth,viewport:document.documentElement.clientWidth}));
    const unknown = await p.evaluate(() => {
      window.pnx.on({kind:'resources',running:2,queued:12});
      return !!document.querySelector('#resources')?.getClientRects().length;
    });
    const violations={recreatedRows:!desktop.sameA||!desktop.sameC,missingConcurrentRows:desktop.running!==2,
      badPercent:!desktop.a.includes('9%')||!desktop.c.includes('37%'),
      unwantedResources:desktop.resourceVisible||unknown,
      readerReset:desktop.query!=='needle'||desktop.scroll!==700,
      overflow:desktop.width>desktop.viewport||mobile.width>mobile.viewport,
      pageErrors:errors.length!==0};
    if(Object.values(violations).some(Boolean))throw new Error('UI_RESOURCES_RED '+JSON.stringify({violations,desktop,mobile}));
    return {ok:true,frontendFixtureOnly:true,desktop,mobile,unknown};
  } finally {await context.close();}
}
