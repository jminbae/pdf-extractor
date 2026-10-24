async function uiQueueRegression(page, baseURL) {
  // Frontend-only fixture: queue events and native responses are synthetic.
  const context = await page.context().browser().newContext({viewport:{width:1336,height:900}});
  const p = await context.newPage();
  try {
    await p.goto(baseURL);
    await p.waitForSelector('.file-row');
    await p.locator('[data-check="2"]').check();
    await p.locator('.file-row[data-index="1"] [data-action=read]').evaluate(n=>n.click());
    await p.waitForSelector('#document h1');
    await p.evaluate(() => {
      window.__queueMode=true;
      window.pnx.on({kind:'run',on:true});
      window.pnx.on({kind:'prog',path:window.__fixture.files[0].path,name:'A.pdf',file_pct:4,pct:4,stage:'문서 구조 추출',page:1,pages:30});
    });
    await p.locator('#search').fill('needle');
    await p.locator('#search-form button[type=submit]').click();
    await p.waitForSelector('.search-hit');
    await p.evaluate(() => document.querySelector('#result-scroll').scrollTop=700);
    const before = await p.evaluate(() => ({disabled:document.querySelector('#extract').disabled,
      label:document.querySelector('#extract').textContent,query:document.querySelector('#search').value,
      scroll:document.querySelector('#result-scroll').scrollTop}));
    if(before.disabled||before.label!=='MD 추출')throw new Error('UI_QUEUE_RED '+JSON.stringify(before));
    await p.locator('#extract').click();
    await p.waitForFunction(() => window.__calls.some(c=>c.name==='extract_selected'));
    const sent = await p.evaluate(() => window.__calls.find(c=>c.name==='extract_selected').args);
    await p.evaluate(() => {
      const files=window.__fixture.files.map((f,i)=>({...f,status:i===0?'running':i===2?'queued':f.status,
        ...(i===0?{progress_pct:4,progress_stage:'문서 구조 추출',progress_page:1,progress_pages:30}:{})}));
      window.pnx.on({kind:'queued',files,count:1});
    });
    const afterQueue = await p.evaluate(() => ({query:document.querySelector('#search').value,
      scroll:document.querySelector('#result-scroll').scrollTop,readerVisible:!document.querySelector('#reader').hidden,
      activePct:document.querySelector('.file-row[data-index="0"] .status-text').textContent,
      pendingStatus:document.querySelector('.file-row[data-index="2"]').dataset.status,
      duplicateDisabled:document.querySelector('#extract').disabled}));
    await p.evaluate(() => document.querySelector('#extract').click());
    const callsAfterDuplicate = await p.evaluate(() => window.__calls.filter(c=>c.name==='extract_selected').length);
    await p.evaluate(() => window.pnx.on({kind:'folder',files:[
      {...window.__fixture.files[0],status:'running',progress_pct:4,progress_stage:'문서 구조 추출',progress_page:1,progress_pages:30},window.__fixture.files[1],
      {...window.__fixture.files[2],status:'queued'},
      {name:'D.pdf',path:window.__fixture.path+'/D.pdf',done:false,md_ready:false,status:'ready'}],path:window.__fixture.path}));
    // The new file is never selected automatically. This queue seam explicitly selects it.
    await p.evaluate(() => document.querySelector('[data-check="3"]').click());
    const afterAdd = await p.evaluate(() => ({calls:window.__calls.filter(c=>c.name==='extract_selected').length,
      disabled:document.querySelector('#extract').disabled,label:document.querySelector('#extract').textContent}));
    await p.evaluate(() => window.__rejectNext=true);
    await p.evaluate(() => document.querySelector('#extract').click());
    await p.waitForFunction(() => window.__calls.filter(c=>c.name==='extract_selected').length===2&&!document.querySelector('#extract').disabled);
    const rejectedCleared = await p.locator('#extract').isEnabled();
    await p.evaluate(() => window.pnx.on({kind:'file_error',path:window.__fixture.files[2].path,error:'Fixture failure'}));
    const failedRetry = await p.evaluate(() => ({status:document.querySelector('.file-row[data-index="2"]').dataset.status,
      retryEnabled:!document.querySelector('.file-row[data-index="2"] [data-action=retry]').disabled,
      mdDisabled:document.querySelector('.file-row[data-index="2"] [data-action=read]').disabled,
      query:document.querySelector('#search').value,scroll:document.querySelector('#result-scroll').scrollTop}));
    await p.locator('#stop').click();
    const stoppingDisabled = await p.locator('#extract').isDisabled();
    const proof={before,sent,afterQueue,callsAfterDuplicate,afterAdd,rejectedCleared,failedRetry,stoppingDisabled};
    const violations={wrongSelection:JSON.stringify(sent[0])!=='[2]',
      readerReset:afterQueue.query!==before.query||afterQueue.scroll!==before.scroll||!afterQueue.readerVisible,
      activeProgressReset:!afterQueue.activePct.includes('4%'),
      queueNotShown:afterQueue.pendingStatus!=='queued',duplicateAllowed:!afterQueue.duplicateDisabled||callsAfterDuplicate!==1,
      autoQueuedFileAddition:afterAdd.calls!==1,newFileCannotQueue:afterAdd.disabled||afterAdd.label!=='MD 추출',
      rejectedInflightStuck:!rejectedCleared,
      failedInflightStuck:failedRetry.status!=='failed'||!failedRetry.retryEnabled||!failedRetry.mdDisabled,
      failureResetsReader:failedRetry.query!==before.query||failedRetry.scroll!==before.scroll,
      stoppingAllowsQueue:!stoppingDisabled};
    if(Object.values(violations).some(Boolean))throw new Error('UI_QUEUE_RED '+JSON.stringify({violations,proof}));
    return {ok:true,frontendFixtureOnly:true,proof};
  } finally {await context.close();}
}
