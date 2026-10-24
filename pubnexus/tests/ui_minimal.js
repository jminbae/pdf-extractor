async function uiMinimalRegression(page, baseURL) {
  // Actual UI JS, synthetic ui_background.py responses only; no user files or extraction.
  const context = await page.context().browser().newContext({viewport:{width:1336,height:900}});
  const p = await context.newPage();
  try {
    await p.goto(baseURL);
    await p.locator('.file-row[data-index="1"] [data-action=read]').evaluate(n=>n.click());
    await p.waitForSelector('#document h1');
    await p.locator('#compare-tab').click();
    await p.waitForSelector('#source-scroll .pdf-page');
    await p.evaluate(() => {
      document.querySelector('#linked-scroll').checked=false;
      document.querySelector('#result-scroll').scrollTop=700;
      window.__minimalDoc=document.querySelector('#document');
      window.__minimalHeading=document.querySelector('#document h1');
    });
    const before=await p.evaluate(() => ({percent:document.querySelector('#md-zoom-reset')?.textContent,
      toolbar:document.querySelector('.toolbar').getBoundingClientRect().height,
      sourceWidth:document.querySelector('.pdf-page').getBoundingClientRect().width}));
    const anchor=await p.evaluate(() => {
      const pane=document.querySelector('#result-scroll').getBoundingClientRect();
      const n=[...document.querySelectorAll('#document p')].find(n=>n.getBoundingClientRect().top>pane.top+40);
      window.__minimalAnchor=n;
      if(!n)return null;const r=n.getBoundingClientRect();
      return {text:n.textContent,top:r.top,x:r.left+20,y:r.top+r.height/2};
    });
    if(!anchor)throw new Error('UI_MINIMAL_FIXTURE_HAS_NO_VISIBLE_PARAGRAPH');
    const {x,y}=anchor;
    await p.mouse.move(x,y);
    await p.keyboard.down('Control');
    try {await p.mouse.wheel(0,-100);} finally {await p.keyboard.up('Control');}
    await p.waitForTimeout(100);
    const zoomed=await p.evaluate(() => ({percent:document.querySelector('#md-zoom-reset')?.textContent,
      zoom:Number(getComputedStyle(document.querySelector('#document')).zoom),
      sameDocument:window.__minimalDoc===document.querySelector('#document'),
      sameHeading:window.__minimalHeading===document.querySelector('#document h1'),
      anchorText:window.__minimalAnchor?.textContent,anchorTop:window.__minimalAnchor?.getBoundingClientRect().top,
      toolbar:document.querySelector('.toolbar').getBoundingClientRect().height,
      sourceWidth:document.querySelector('.pdf-page').getBoundingClientRect().width}));
    if(before.percent?.trim()!=='100%'||zoomed.percent?.trim()!=='110%'||zoomed.zoom!==1.1)
      throw new Error('UI_MINIMAL_ZOOM_RED '+JSON.stringify({before,zoomed,anchor}));
    if(!anchor||!zoomed.sameDocument||!zoomed.sameHeading||anchor.text!==zoomed.anchorText||
      Math.abs(anchor.top-zoomed.anchorTop)>6||Math.abs(before.toolbar-zoomed.toolbar)>1||
      Math.abs(before.sourceWidth-zoomed.sourceWidth)>1)
      throw new Error('UI_MINIMAL_ZOOM_POSITION_RED '+JSON.stringify({before,zoomed,anchor}));
    // At 110%, source→MD uses the current scaled DOM geometry, then MD→source stays exact.
    await p.locator('#source-scroll').evaluate(n=>{n.scrollTop=0;});
    const sourcePage=await p.locator('.pdf-page').first().boundingBox();
    await p.mouse.click(sourcePage.x+sourcePage.width*.1,sourcePage.y+sourcePage.height*.09);
    await p.waitForSelector('.pdf-highlight');
    const mapping110=await p.evaluate(() => ({
      blockOffset:document.querySelector('#document [data-block-id="b-old"]').getBoundingClientRect().top-
        document.querySelector('#result-scroll').getBoundingClientRect().top,
      top:document.querySelector('.pdf-highlight').style.top,
      left:document.querySelector('.pdf-highlight').style.left,
      percent:document.querySelector('#md-zoom-reset').textContent}));
    if(Math.abs(mapping110.blockOffset-24)>2||mapping110.top!=='6%'||mapping110.left!=='5%'||mapping110.percent.trim()!=='110%')
      throw new Error('UI_MINIMAL_SCALED_SOURCE_TO_MD_RED '+JSON.stringify(mapping110));
    await p.locator('#document [data-block-id="b-old"]').click();
    const reverse110=await p.evaluate(() => ({page:document.querySelector('#page-input').value,
      highlight:document.querySelector('.pdf-highlight').style.top}));
    if(reverse110.page!=='1'||reverse110.highlight!=='6%')throw new Error('UI_MINIMAL_SCALED_MD_TO_SOURCE_RED '+JSON.stringify(reverse110));
    await p.locator('#md-zoom-reset').click();
    const reset=await p.evaluate(() => ({percent:document.querySelector('#md-zoom-reset').textContent,
      zoom:Number(getComputedStyle(document.querySelector('#document')).zoom),
      sameHeading:window.__minimalHeading===document.querySelector('#document h1')}));
    if(reset.percent.trim()!=='100%'||reset.zoom!==1||!reset.sameHeading)
      throw new Error('UI_MINIMAL_RESET_RED '+JSON.stringify(reset));
    // A normal wheel scrolls; Ctrl-wheel on the original scales only its own pane.
    await p.mouse.move(x,y);const oldScroll=await p.locator('#result-scroll').evaluate(n=>n.scrollTop);
    await p.mouse.wheel(0,250);await p.waitForTimeout(150);
    const ordinary=await p.locator('#result-scroll').evaluate(n=>n.scrollTop);
    const original=await p.evaluate(() => {
      const e=new WheelEvent('wheel',{ctrlKey:true,deltaY:-100,bubbles:true,cancelable:true});
      document.querySelector('#source-scroll').dispatchEvent(e);
      return {prevented:e.defaultPrevented,percent:document.querySelector('#md-zoom-reset').textContent,
        sourcePercent:document.querySelector('#source-zoom-reset').textContent};
    });
    if(!(ordinary>oldScroll)||!original.prevented||original.percent.trim()!=='100%'||original.sourcePercent.trim()!=='110%')
      throw new Error('UI_MINIMAL_WHEEL_SCOPE_RED '+JSON.stringify({oldScroll,ordinary,original}));
    await p.locator('#source-zoom-reset').click();
    await p.locator('#document [data-block-id="b-old"]').click();
    await p.waitForSelector('.pdf-highlight');
    const mapping=await p.locator('.pdf-highlight').first().evaluate(n=>({top:n.style.top,left:n.style.left}));
    if(mapping.top!=='6%'||mapping.left!=='5%')throw new Error('UI_MINIMAL_ZOOM_MAPPING_RED '+JSON.stringify(mapping));
    const mappedLight=await p.locator('#document .mapped-active').evaluate(n=>getComputedStyle(n).backgroundColor);
    if(mappedLight==='rgba(0, 0, 0, 0)')throw new Error('UI_MINIMAL_MAPPED_LIGHT_BACKGROUND_RED');
    const closed=[];
    for(const mode of ['content','source','compare']) {
      await p.locator('#'+mode+'-tab').click();
      if(await p.locator('#'+mode+'-tab').getAttribute('aria-pressed')!=='true')
        throw new Error('UI_MINIMAL_MODE_RED '+mode);
      await p.evaluate(() => window.pnx.on({kind:'run',on:true}));
      const close=p.locator('#reader-close');
      if(await close.count()!==1)throw new Error('UI_MINIMAL_CLOSE_RED '+mode+': viewer has no upper-right X');
      const closeBox=await close.boundingBox(),readerBox=await p.locator('#reader').boundingBox();
      await close.click();
      const result=await p.evaluate(() => ({list:!document.querySelector('#list-view').hidden,
        reader:document.querySelector('#reader').hidden,stopEnabled:!document.querySelector('#stop').disabled,
        cancels:window.__calls.filter(c=>c.name==='cancel').length}));
      if(!result.list||!result.reader||!result.stopEnabled||result.cancels||
        closeBox.y<readerBox.y||closeBox.y-readerBox.y>8||
        closeBox.x+closeBox.width>readerBox.x+readerBox.width||readerBox.x+readerBox.width-closeBox.x-closeBox.width>12)
        throw new Error('UI_MINIMAL_CLOSE_BEHAVIOR_RED '+JSON.stringify({mode,result,closeBox,readerBox}));
      closed.push({mode,...result});
      await p.locator('.file-row[data-index="1"] [data-action=read]').evaluate(n=>n.click());
      await p.waitForSelector('#document h1');
    }
    if(await p.locator('#theme-toggle').count()!==1)throw new Error('UI_MINIMAL_THEME_RED theme toggle is missing');
    await p.evaluate(() => {window.__themeHeading=document.querySelector('#document h1');});
    const light=await p.evaluate(() => ({background:getComputedStyle(document.body).backgroundColor,
      pressed:document.querySelector('#theme-toggle').getAttribute('aria-pressed')}));
    await p.locator('#theme-toggle').click();
    await p.waitForFunction(() => document.querySelector('#theme-toggle').getAttribute('aria-pressed')==='true');
    await p.locator('#document [data-block-id="b-old"]').click();
    await p.waitForSelector('.pdf-highlight');
    const dark=await p.evaluate(() => ({background:getComputedStyle(document.body).backgroundColor,
      scheme:getComputedStyle(document.documentElement).colorScheme,
      sameHeading:window.__themeHeading===document.querySelector('#document h1'),
      imageFilter:getComputedStyle(document.querySelector('.pdf-page img')).filter,
      mappedBackground:getComputedStyle(document.querySelector('#document .mapped-active')).backgroundColor,
      calls:window.__calls.filter(c=>c.name==='set_theme')}));
    if(light.background===dark.background||!dark.sameHeading||dark.scheme!=='dark'||dark.imageFilter!=='none'||dark.mappedBackground==='rgba(0, 0, 0, 0)'||
      !dark.calls.some(c=>c.args[0]==='dark'))throw new Error('UI_MINIMAL_DARK_RED '+JSON.stringify({light,dark}));
    await p.reload();await p.waitForSelector('.file-row');
    try {await p.waitForFunction(() => document.querySelector('#theme-toggle').getAttribute('aria-pressed')==='true',null,{timeout:3000});}
    catch {throw new Error('UI_MINIMAL_SAVED_DARK_RED '+JSON.stringify(await p.evaluate(() => ({
      saved:window.__fixture.settings.theme,theme:document.documentElement.dataset.theme,
      pressed:document.querySelector('#theme-toggle').getAttribute('aria-pressed')}))));}
    const restored=await p.evaluate(() => ({pressed:document.querySelector('#theme-toggle').getAttribute('aria-pressed'),
      background:getComputedStyle(document.body).backgroundColor}));
    await p.evaluate(() => {window.__themeRejectNext=true;});
    await p.locator('#theme-toggle').click();
    await p.waitForFunction(() => document.querySelector('#message').textContent.includes('테마 저장 실패 fixture'));
    const failed=await p.evaluate(() => ({pressed:document.querySelector('#theme-toggle').getAttribute('aria-pressed'),
      disabled:document.querySelector('#theme-toggle').disabled,
      saved:window.__fixture.settings.theme,messageMax:getComputedStyle(document.querySelector('#message')).maxHeight,
      messageOverflow:getComputedStyle(document.querySelector('#message')).overflowY}));
    if(restored.background!==dark.background||failed.pressed!=='true'||failed.disabled||failed.saved!=='dark'||
      failed.messageMax!=='95px'||failed.messageOverflow!=='auto')
      throw new Error('UI_MINIMAL_THEME_RESTORE_OR_FAILURE_RED '+JSON.stringify({restored,failed}));
    await p.locator('#theme-toggle').click();
    await p.waitForFunction(() => document.querySelector('#theme-toggle').getAttribute('aria-pressed')==='false');
    await p.evaluate(() => window.pnx.on({kind:'mark',path:window.__fixture.files[1].path,
      status:'partial',md_ready:true,warnings:['OCR 결과 확인 필요'],
      warning_details:['OCR fixture: 첫 줄\n원문 확인이 필요한 실제 경고','표 fixture: 병합 셀 확인 필요']}));
    const info=p.locator('.file-row[data-index="1"] [data-action=details]');
    await info.click();await p.waitForSelector('#file-dialog[open]');
    await p.locator('#properties details summary').click();
    await p.waitForSelector('#properties details[open] li');
    const details=await p.locator('#properties').textContent();
    if(!details.includes('OCR fixture: 첫 줄\n원문 확인이 필요한 실제 경고')||!details.includes('표 fixture: 병합 셀 확인 필요'))
      throw new Error('UI_MINIMAL_WARNINGS_LOST '+details);
    await p.keyboard.press('Escape');
    const infoClosed=await p.evaluate(() => ({closed:!document.querySelector('#file-dialog').open,
      focus:document.activeElement.getAttribute('aria-label')}));
    if(!infoClosed.closed||infoClosed.focus!=='B.pdf 파일 정보')throw new Error('UI_MINIMAL_DETAILS_ESCAPE_RED '+JSON.stringify(infoClosed));
    await info.click();await p.locator('[data-action=file-close]').click();
    if(await p.locator('#file-dialog').evaluate(n=>n.open))throw new Error('UI_MINIMAL_DETAILS_X_RED');
    await p.locator('.file-row[data-index="1"] [data-action=read]').evaluate(n=>n.click());
    await p.waitForSelector('#document h1');
    const limits=await p.evaluate(() => {
      const pane=document.querySelector('#result-scroll'),send=delta=>{const e=new WheelEvent('wheel',{ctrlKey:true,deltaY:delta,bubbles:true,cancelable:true});pane.dispatchEvent(e);return e.defaultPrevented;};
      for(let i=0;i<30;i++)send(-100);const max=document.querySelector('#md-zoom-reset').textContent;
      const capped=send(-100);for(let i=0;i<30;i++)send(100);
      return {max,min:document.querySelector('#md-zoom-reset').textContent,capped};
    });
    if(limits.max!=='250%'||limits.min!=='50%'||!limits.capped)throw new Error('UI_MINIMAL_ZOOM_LIMIT_RED '+JSON.stringify(limits));
    await p.locator('#md-zoom-reset').click();
    return {ok:true,frontendFixtureOnly:true,proof:{before,anchor,zoomed,mapping110,reverse110,reset,ordinary,original,mapping,closed,light,dark,restored,failed,details,infoClosed,limits}};
  } finally {await context.close();}
}
