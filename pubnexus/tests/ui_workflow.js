async function uiWorkflowRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1336,height:900}}),p=await context.newPage();
  try{
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    const complete=p.locator('.file-row[data-index="1"] [data-check]');
    if(!await complete.isEnabled())throw new Error('UI_WORKFLOW_COMPLETE_CHECK_DISABLED_RED');
    await p.locator('#select-all').check();await p.locator('#select-all').uncheck();await complete.check();
    await p.locator('#extract').click();
    await p.waitForFunction(()=>window.__calls.some(c=>c.name==='extract_selected'));
    const selected=await p.evaluate(()=>window.__calls.find(c=>c.name==='extract_selected').args);
    if(JSON.stringify(selected.slice(0,2))!=='[[1],true]')throw new Error('UI_WORKFLOW_COMPLETE_REEXTRACT_RED '+JSON.stringify(selected));
    await p.evaluate(()=>{window.pnx.on({kind:'run',on:true});window.pnx.on({kind:'prog',path:window.__fixture.files[1].path,file_pct:20});});
    await complete.uncheck();await complete.check();
    if(!await p.locator('#extract').isDisabled())throw new Error('UI_WORKFLOW_DUPLICATE_JOB_RED');
    await p.evaluate(()=>window.pnx.on({kind:'mark',path:window.__fixture.files[1].path,status:'partial',md_ready:true}));
    if(!await complete.isChecked()||!await complete.isEnabled())throw new Error('UI_WORKFLOW_COMPLETION_DROPS_CHECK_RED');
    await p.locator('#select-all').check();
    if(await p.locator('[data-check]:checked').count()!==3)throw new Error('UI_WORKFLOW_ALL_CHECK_RED');
    await p.evaluate(()=>window.pnx.on({kind:'run',on:false,files:window.__fixture.files}));
    await p.locator('.file-row[data-index="0"]').dblclick();await p.waitForSelector('#source-scroll .pdf-page');
    await p.evaluate(()=>window.__sourceFirst=document.querySelector('.pdf-page'));
    await p.locator('#reader-extract').click();
    await p.evaluate(()=>window.pnx.on({kind:'mark',path:window.__fixture.files[0].path,status:'complete',md_ready:true}));
    await p.waitForFunction(()=>document.querySelector('#reader').classList.contains('compare'));
    await p.waitForSelector('#document h1');
    const comparison=await p.evaluate(()=>({sameSource:window.__sourceFirst===document.querySelector('.pdf-page'),source:!document.querySelector('#source-pane').hidden,result:!document.querySelector('#result-pane').hidden}));
    if(!comparison.sameSource||!comparison.source||!comparison.result)throw new Error('UI_WORKFLOW_AUTOCOMPARE_RED '+JSON.stringify(comparison));
    await p.locator('#content-tab').click();await p.locator('#search').fill('retain snapshot');
    await p.evaluate(()=>{window.__savedHeading=document.querySelector('#document h1');document.querySelector('#result-scroll').scrollTop=600;window.pnx.on({kind:'mark',path:window.__fixture.files[0].path,status:'complete',md_ready:true});window.pnx.on({kind:'mark',path:window.__fixture.files[2].path,status:'complete',md_ready:true});});
    const retained=await p.evaluate(()=>({same:window.__savedHeading===document.querySelector('#document h1'),query:document.querySelector('#search').value,scroll:document.querySelector('#result-scroll').scrollTop,compare:document.querySelector('#reader').classList.contains('compare')}));
    if(!retained.same||retained.query!=='retain snapshot'||retained.scroll!==600||retained.compare)throw new Error('UI_WORKFLOW_SNAPSHOT_RESET_RED '+JSON.stringify(retained));
    return{ok:true,selected,comparison,retained};
  }finally{await context.close();}
}
