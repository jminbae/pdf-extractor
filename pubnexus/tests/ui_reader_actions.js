async function uiReaderActionsRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1336,height:900}}),p=await context.newPage();
  try{
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="0"]').dblclick();await p.waitForSelector('.pdf-page');
    const layout=await p.evaluate(()=>{const action=document.querySelector('#reader-extract').getBoundingClientRect(),tools=document.querySelector('.reader-tools').getBoundingClientRect(),source=document.querySelector('#source-tab').getBoundingClientRect();return {right:tools.right-action.right,sourceLeft:source.left,actionLeft:action.left};});
    if(layout.right>16||layout.actionLeft<=layout.sourceLeft)throw new Error('UI_READER_ACTION_RIGHT_RED '+JSON.stringify(layout));
    await p.locator('#reader-extract').click();
    const request=await p.evaluate(()=>window.__calls.filter(c=>c.name==='extract_selected').at(-1));
    if(JSON.stringify(request.args[2])!==JSON.stringify([await p.evaluate(()=>window.__fixture.files[0].path)]))throw new Error('UI_READER_EXPECTED_PATHS_RED '+JSON.stringify(request));
    await p.evaluate(()=>{window.pnx.on({kind:'run',on:true});window.pnx.on({kind:'prog',path:window.__fixture.files[0].path,file_pct:37});});const running=await p.locator('#reader-extract').evaluate(n=>({busy:n.getAttribute('aria-busy'),text:n.textContent,gauge:parseFloat(getComputedStyle(n,'::after').width),width:n.getBoundingClientRect().width}));if(running.busy!=='true'||!running.text.includes('37%')||running.gauge<running.width*.3||running.gauge>running.width*.45)throw new Error('UI_READER_ACTION_GAUGE_RED '+JSON.stringify(running));
    await p.evaluate(()=>window.pnx.on({kind:'mark',path:window.__fixture.files[0].path,status:'complete',md_ready:true,needs_refresh:false}));
    await p.waitForSelector('#document h1');
    const saved=await p.evaluate(()=>({text:document.querySelector('#reader-extract').textContent,source:document.querySelector('#source-pane').getBoundingClientRect(),result:document.querySelector('#result-pane').getBoundingClientRect()}));
    if(saved.text!=='MD 보기'||saved.source.x>=saved.result.x)throw new Error('UI_READER_ACTION_MD_RED '+JSON.stringify(saved));
    await p.locator('#source-tab').click();await p.locator('#reader-extract').click();
    if(!await p.locator('#reader').evaluate(n=>n.classList.contains('compare')))throw new Error('UI_READER_ACTION_MD_OPEN_RED');
    const calls=await p.evaluate(()=>window.__calls.filter(c=>c.name==='extract_selected').length);
    if(calls!==1)throw new Error('UI_READER_MD_REEXTRACTS_RED '+calls);
    const narrow=[];for(const width of [800,720,620]){await p.setViewportSize({width,height:520});narrow.push(await p.evaluate(()=>{const s=document.querySelector('#source-pane').getBoundingClientRect(),r=document.querySelector('#result-pane').getBoundingClientRect(),b=document.querySelector('#reader-extract').getBoundingClientRect();return{width:innerWidth,sourceLeft:s.left,resultLeft:r.left,resultWidth:r.width,buttonRight:b.right,overflow:document.documentElement.scrollWidth>innerWidth};}));}if(narrow.some(n=>n.sourceLeft!==0||n.resultLeft<=0||n.resultWidth<250||n.buttonRight>n.width||n.overflow))throw new Error('UI_READER_NARROW_COMPARE_RED '+JSON.stringify(narrow));
    return{ok:true,narrow,layout,request,saved:{text:saved.text,sourceLeft:saved.source.x,resultLeft:saved.result.x},calls};
  }finally{await context.close();}
}
