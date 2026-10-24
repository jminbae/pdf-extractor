async function uiBackgroundRegression(page, baseURL) {
  // Run only against ui_background.py's private synthetic frontend fixture.
  const context = await page.context().browser().newContext({viewport:{width:1336,height:900}});
  const p = await context.newPage();
  try {
    await p.goto(baseURL);
    await p.waitForSelector('.file-row');
    await p.locator('.file-row[data-index="1"] [data-action=read]').click();
    await p.waitForSelector('#document h1');
    await p.evaluate(() => {
      window.pnx.on({kind:'run',on:true});
      window.pnx.on({kind:'prog',path:window.__fixture.files[0].path,name:'A.pdf',file_pct:4,pct:4,stage:'서지정보 조회',page:1,pages:30});
    });
    const busy = await p.evaluate(() => ({
      addFilesDisabled:document.querySelector('[data-action=add-files]').disabled,
      legacyFolderButton:!!document.querySelector('[data-action=add-folder]'),
      checkboxes:[...document.querySelectorAll('[data-check]')].map(n=>n.disabled),
      otherSavedMdDisabled:document.querySelector('.file-row[data-index="1"] [data-action=read]').disabled,
      readerSourceOnly:document.querySelector('#reader').classList.contains('source-only'),
      searchDisabled:document.querySelector('#search').disabled,
      duplicateExtractionDisabled:document.querySelector('#extract').disabled
    }));
    await p.locator('#search').fill('needle');
    await p.locator('#search-form button[type=submit]').click();
    await p.waitForSelector('.search-hit');
    await p.evaluate(() => {document.querySelector('#result-scroll').scrollTop=700;});
    const before = await p.evaluate(() => ({query:document.querySelector('#search').value,scroll:document.querySelector('#result-scroll').scrollTop}));
    await p.evaluate(() => window.pnx.on({kind:'folder',files:[...window.__fixture.files,
      {name:'D.pdf',path:window.__fixture.path+'/D.pdf',status:'ready',md_ready:false,done:false}],path:window.__fixture.path}));
    const afterAppend = await p.evaluate(() => ({query:document.querySelector('#search').value,
      scroll:document.querySelector('#result-scroll').scrollTop,readerVisible:!document.querySelector('#reader').hidden}));
    await p.evaluate(() => window.pnx.on({kind:'run',on:false,done:1,files:window.__fixture.files}));
    await p.waitForSelector('#document h1');
    const after = await p.evaluate(() => ({query:document.querySelector('#search').value,scroll:document.querySelector('#result-scroll').scrollTop}));

    await p.locator('#reader-back').click();
    await p.evaluate(() => {
      window.pnx.on({kind:'run',on:true});
      window.pnx.on({kind:'prog',path:window.__fixture.files[0].path,name:'A.pdf',file_pct:4,pct:4});
    });
    await p.locator('.file-row[data-index="2"]').click();
    await p.locator('.file-row[data-index="1"]').dblclick();
    await p.waitForSelector('#source-scroll .pdf-page');
    await p.locator('#search').fill('needle');
    await p.locator('#search-form button[type=submit]').click();
    await p.waitForSelector('.search-hit');
    const source = await p.evaluate(() => ({sourceOnly:document.querySelector('#reader').classList.contains('source-only'),
      page:document.querySelector('#page-input').value,sourceScroll:document.querySelector('#source-scroll').scrollTop,
      calls:window.__calls.filter(c=>c.name.startsWith('search'))}));

    await p.locator('#reader-back').click();
    await p.evaluate(() => window.pnx.on({kind:'run',on:false,done:1,files:window.__fixture.files}));
    await p.locator('.file-row[data-index="1"] [data-action=read]').click();
    await p.waitForSelector('#document h1');
    // Exercise the real delegated retry handler while its existing MD is open.
    await p.evaluate(() => document.querySelector('#properties [data-action=retry]').click());
    await p.waitForTimeout(100);
    await p.evaluate(() => {
      window.pnx.on({kind:'run',on:true});
      window.pnx.on({kind:'prog',path:window.__fixture.files[1].path,name:'B.pdf',file_pct:4,pct:4});
    });
    const retry = await p.evaluate(() => ({sourceOnly:document.querySelector('#reader').classList.contains('source-only'),
      savedMdDisabled:document.querySelector('.file-row[data-index="1"] [data-action=read]').disabled,
      rowStatus:document.querySelector('.file-row[data-index="1"]').dataset.status,
      unsavedMdDisabled:document.querySelector('.file-row[data-index="2"] [data-action=read]').disabled,
      calls:window.__calls.filter(c=>c.name==='extract_selected')}));

    await p.evaluate(() => {
      window.pnx.on({kind:'mark',path:window.__fixture.files[1].path,status:'failed',md_ready:true,error:'Fixture retry failed; previous MD remains'});
      window.pnx.on({kind:'mark',path:window.__fixture.files[2].path,status:'paused',md_ready:false});
    });
    const failure = await p.evaluate(() => ({
      oldSavedMdDisabled:document.querySelector('.file-row[data-index="1"] [data-action=read]').disabled,
      unsavedMdDisabled:document.querySelector('.file-row[data-index="2"] [data-action=read]').disabled,
      oldSavedStatus:document.querySelector('.file-row[data-index="1"]').dataset.status
    }));
    // A newly published backend must not supply new coordinates to this old DOM.
    await p.evaluate(() => {window.__revision='saved-B-v2';document.querySelector('#result-scroll').scrollTop=0;});
    await p.locator('#document [data-block-id="b-old"]').click();
    await p.waitForSelector('.pdf-highlight');
    const oldMapping = await p.evaluate(() => ({
      nativeLocationCalls:window.__calls.filter(c=>c.name==='location').length,
      highlightTop:document.querySelector('.pdf-highlight').style.top,
      highlightLeft:document.querySelector('.pdf-highlight').style.left
    }));
    await p.locator('#search').fill('needle');
    await p.locator('#search-form button[type=submit]').click();
    await p.waitForFunction(() => document.querySelector('#search-results').textContent.includes('다시 열어'));
    const staleSearch = await p.locator('#search-results').textContent();
    const proof={busy,beforeRunComplete:before,afterAppend,afterRunComplete:after,sourceControls:source,forcedRetrySavedMd:retry,failure,oldMapping,staleSearch};
    const violations={
      fileInputBlocked:busy.addFilesDisabled||busy.legacyFolderButton,
      newDocumentSelectionBlocked:busy.checkboxes[2],
      unrelatedReaderReset:before.query!==after.query||before.scroll!==after.scroll,
      appendClosesReader:!afterAppend.readerVisible||before.query!==afterAppend.query||before.scroll!==afterAppend.scroll,
      savedMdBlockedDuringRetry:retry.savedMdDisabled,
      forcedReaderSwitch:retry.sourceOnly,
      pendingDocumentCannotQueue:busy.duplicateExtractionDisabled,
      sourceSearchBroken:!source.sourceOnly||source.page!=='3'||!(source.sourceScroll>0),
      unsavedMdEnabled:!retry.unsavedMdDisabled||!failure.unsavedMdDisabled,
      priorSavedMdLostAfterFailure:failure.oldSavedMdDisabled,
      fakeCompleteStatus:retry.rowStatus!=='running'||failure.oldSavedStatus==='complete',
      oldSnapshotUsedNewMapping:oldMapping.nativeLocationCalls!==0||oldMapping.highlightTop!=='6%'||oldMapping.highlightLeft!=='5%',
      staleSearchMixedNewResult:!staleSearch.includes('다시 열어')
    };
    if(Object.values(violations).some(Boolean))throw new Error('UI_BACKGROUND_RED '+JSON.stringify({violations,proof}));
    return {ok:true,frontendFixtureOnly:true,proof};
  } finally {await context.close();}
}
