async function uiExplorerRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage(),errors=[];
  p.on('pageerror',e=>errors.push(String(e)));
  try{
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    await p.evaluate(()=>{
      const api=window.pywebview.api,root=window.__fixture.path;
      window.__visits=[];window.__sourceOpens=[];
      const state=path=>({ok:true,path,parent:root,folders:path===root?[{name:'Child',path:root+'/Child'}]:[],files:path===root?window.__fixture.files:[{name:'Nested.pdf',path:path+'/Nested.pdf',status:'ready',md_ready:false}],navigation:{shortcuts:[{name:'문서',path:root}],drives:[{name:'D:',path:root}]}});
      window.pywebview.api=new Proxy(api,{get(target,name){if(name==='browse_folder')return async path=>{window.__visits.push(path);return state(path);};return target[name];}});
      window.pnx.on({kind:'folder',...state(root)});
    });
    const ordering=await p.evaluate(()=>({names:[...document.querySelector('#rows').children].map(n=>n.querySelector('.filename')?.textContent||n.textContent.trim()),left:document.querySelector('#folder-panel').textContent}));
    if(ordering.names[0]!=='..'||ordering.names[1]!=='Child'||ordering.left.includes('하위 폴더')||ordering.left.includes('Child'))throw new Error('EXPLORER_FOLDER_ROWS_RED '+JSON.stringify(ordering));
    const opens=[];p.on('request',r=>{if(r.url().includes('/open?'))opens.push(r.url());});
    await p.locator('.file-row[data-index="0"] .filename').click();await p.waitForSelector('#source-scroll .pdf-page');
    const inline=await p.evaluate(()=>{window.__sourceNode=document.querySelector('.pdf-page');return {list:!document.querySelector('#list-view').hidden,preview:document.querySelector('#reader').classList.contains('inline-preview'),listRight:document.querySelector('.file-list').getBoundingClientRect().right,source:document.querySelector('#source-pane').getBoundingClientRect().x,name:document.querySelector('#reader-name').textContent};});
    if(!inline.list||!inline.preview||inline.source<inline.listRight||inline.name!=='A.pdf'||opens.some(u=>u.includes('mode=result')))throw new Error('EXPLORER_SINGLE_PREVIEW_RED '+JSON.stringify({inline,opens}));
    await p.locator('[data-check="2"]').check();
    if((await p.locator('#reader-name').textContent())!=='A.pdf')throw new Error('EXPLORER_CHECK_CHANGED_PREVIEW_RED');
    await p.locator('#source-scroll').evaluate(n=>n.scrollTop=700);await p.waitForTimeout(30);
    await p.locator('.file-row[data-index="0"] .filename').dblclick();
    const full=await p.evaluate(()=>({list:document.querySelector('#list-view').hidden,inline:document.querySelector('#reader').classList.contains('inline-preview'),same:window.__sourceNode===document.querySelector('.pdf-page'),scroll:document.querySelector('#source-scroll').scrollTop,position:document.querySelector('#source-position').textContent,topInputs:document.querySelector('#source-pane .pane-label').querySelectorAll('#page-input,[data-action^="page-"]').length}));
    if(!full.list||full.inline||!full.same||full.scroll<500||full.topInputs)throw new Error('EXPLORER_FULL_PRESERVE_RED '+JSON.stringify(full));
    await p.keyboard.press('Alt+ArrowLeft');await p.waitForFunction(()=>document.querySelector('#reader').classList.contains('inline-preview'));
    const back=await p.evaluate(()=>({same:window.__sourceNode===document.querySelector('.pdf-page'),scroll:document.querySelector('#source-scroll').scrollTop,checked:document.querySelector('[data-check="2"]').checked}));
    if(!back.same||back.scroll<500||!back.checked)throw new Error('EXPLORER_VIEW_HISTORY_RED '+JSON.stringify(back));
    await p.keyboard.press('Alt+ArrowRight');await p.waitForFunction(()=>document.querySelector('#list-view').hidden);
    await p.evaluate(()=>document.dispatchEvent(new MouseEvent('mouseup',{button:3,bubbles:true,cancelable:true})));await p.waitForFunction(()=>document.querySelector('#reader').classList.contains('inline-preview'));
    await p.locator('#reader-close').click();await p.locator('.folder-row[data-folder-path$="Child"]').dblclick();await p.waitForSelector('.file-row:has-text("Nested.pdf")');
    await p.evaluate(()=>document.dispatchEvent(new MouseEvent('mouseup',{button:3,bubbles:true,cancelable:true})));await p.waitForSelector('.file-row:has-text("A.pdf")');
    if(!await p.locator('[data-check="2"]').isChecked())throw new Error('EXPLORER_FOLDER_HISTORY_CHECK_RED');
    for(const width of [1040,720]){await p.setViewportSize({width,height:520});await p.locator('.file-row[data-index="1"] .filename').click();await p.waitForSelector('#source-scroll .pdf-page');const geometry=await p.evaluate(()=>({overflow:document.documentElement.scrollWidth-innerWidth,list:document.querySelector('.file-list').getBoundingClientRect().width,preview:document.querySelector('#reader').getBoundingClientRect().width,right:document.querySelector('#reader-extract').getBoundingClientRect().right,width:innerWidth}));if(geometry.overflow||geometry.list<180||geometry.preview<250||geometry.right>geometry.width)throw new Error('EXPLORER_NARROW_RED '+JSON.stringify(geometry));}
    if(errors.length)throw new Error('EXPLORER_RUNTIME_RED '+JSON.stringify(errors));
    return {ok:true,ordering,inline,full,back,opens,errors};
  }finally{await context.close();}
}

async function uiTextSourceRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();
  try{
    await p.route('**/open?*',route=>route.fulfill({json:{source_kind:'text',source_ready:true,text:'😀 first line\r\n<img src=x onerror=alert(1)> needle\r\nlast line',encoding:'utf-8',source_revision:'text-v1',revision:'text-v1',pages:[]}}));
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    await p.evaluate(()=>{const api=window.pywebview.api;window.pywebview.api=new Proxy(api,{get(t,n){if(n==='search_source')return async()=>({source_revision:'text-v1',results:[{side:'source',text:'needle',locations:[{kind:'text',line:2,offset:43,length:6}]}]});return t[n];}});});
    await p.locator('.file-row[data-index="0"]').dblclick();await p.waitForSelector('#source-text');
    const raw=await p.locator('#source-text').textContent();if(!raw.includes('<img src=x')||await p.locator('#source-text img').count())throw new Error('TEXT_SOURCE_ESCAPING_RED');
    await p.locator('#search').fill('needle');await p.locator('#search-form').evaluate(n=>n.requestSubmit());await p.waitForSelector('#source-text .text-location');
    if((await p.locator('#source-text mark').textContent())!=='needle')throw new Error('TEXT_SOURCE_CODEPOINT_OFFSET_RED');if(!(await p.locator('#source-position').textContent()).includes('2'))throw new Error('TEXT_SOURCE_LINE_RED');
    await p.locator('#source-scroll').hover();await p.keyboard.down('Control');await p.mouse.wheel(0,-80);await p.keyboard.up('Control');await p.waitForFunction(()=>document.querySelector('#source-zoom-reset').textContent==='110%');
    await p.evaluate(()=>{const api=window.pywebview.api;window.pywebview.api=new Proxy(api,{get(t,n){if(n==='search_source')return async()=>({source_revision:'text-v2',results:[{side:'source',text:'wrong offset',locations:[{kind:'text',line:3,offset:0,length:4}]}]});return t[n];}});});await p.locator('#search-form').evaluate(n=>n.requestSubmit());await p.waitForFunction(()=>document.querySelector('#search-results').textContent.includes('원본이 바뀌'));if((await p.locator('#source-text mark').textContent())!=='needle')throw new Error('TEXT_SOURCE_STALE_REVISION_APPLIED_RED');return {ok:true,raw,line:await p.locator('#source-position').textContent(),staleRejected:true};
  }finally{await context.close();}
}

async function uiExplorerActionsRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();
  try{
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    await p.evaluate(()=>{const root=window.__fixture.path,api=window.pywebview.api;window.__treeReads=[];window.pnx.on({kind:'folder',...window.__fixture,parent:null,folders:[{name:'Child',path:root+'/Child'}],navigation:{shortcuts:[{name:'문서',path:root}],drives:[]}});window.pywebview.api=new Proxy(api,{get(t,n){if(n==='list_folders')return async path=>{window.__treeReads.push(path);return {ok:true,path,folders:[{name:'Grandchild',path:path+'/Grandchild'}]};};return t[n];}});});
    const root=await p.evaluate(()=>window.__fixture.path);
    await p.locator('#folder-shortcuts .tree-toggle').first().click();await p.waitForSelector('#folder-shortcuts .tree-children [data-folder-path$="Child"]');
    await p.locator('#folder-shortcuts .tree-children .tree-toggle').click();await p.waitForSelector('#folder-shortcuts .tree-children .tree-children [data-action="browse-folder"]');
    const tree=await p.evaluate(()=>({reads:window.__treeReads,name:document.querySelector('#folder-name').textContent,files:[...document.querySelectorAll('.file-row .filename')].map(n=>n.textContent)}));
    if(tree.reads.length!==1||!tree.reads[0].endsWith('Child')||tree.files.join()!=='A.pdf,B.pdf,C.pdf')throw new Error('EXPLORER_TREE_MUTATES_LIST_RED '+JSON.stringify(tree));
    await p.locator('.file-row[data-index="1"] .filename').click();await p.waitForSelector('#source-scroll .pdf-page');
    if(await p.locator('#content-tab').isVisible())throw new Error('EXPLORER_DUPLICATE_MD_CTA_RED');
    await p.locator('#reader-md').click();await p.waitForSelector('#document h1');
    const md=await p.evaluate(()=>({list:!document.querySelector('#list-view').hidden,result:!document.querySelector('#result-pane').hidden,source:!document.querySelector('#source-pane').hidden,compare:document.querySelector('#reader').classList.contains('compare')}));
    if(!md.list||!md.result||md.source||md.compare)throw new Error('EXPLORER_SAME_PANE_MD_RED '+JSON.stringify(md));
    await p.locator('#source-tab').click();await p.waitForSelector('#source-scroll .pdf-page');
    await p.evaluate(()=>{for(const i of [0,2])window.pnx.on({kind:'prog',path:window.__fixture.files[i].path,file_pct:37});});
    if(await p.locator('.file-row[data-status="running"]').count()!==2||await p.locator('.status:visible,.row-gauge:visible').count())throw new Error('VIEWER_BACKGROUND_STATE_DISCLOSURE_RED');
    await p.evaluate(()=>window.pnx.on({kind:'mark',path:window.__fixture.files[1].path,status:'partial',md_ready:true,warnings:['OCR을 사용했습니다.'],warning_details:['표 3의 마지막 행은 이미지 해상도 때문에 읽지 못했습니다.']}));
    if((await p.locator('.file-row[data-index="1"] .status-text').textContent())!=='저장 완료')throw new Error('EXPLORER_SAVED_LABEL_RED');
    await p.locator('#reader-name').click();await p.waitForFunction(()=>document.querySelector('#file-dialog').open);
    if(await p.locator('#properties .notice-details[open]').count()||await p.locator('#properties .notice-summary').count()||await p.locator('#properties .notice-details').count()!==1)throw new Error('EXPLORER_WARNING_DISCLOSURE_RED');
    await p.locator('#properties .notice-details summary').click();if(!(await p.locator('#properties .notice-details').textContent()).includes('표 3의 마지막 행'))throw new Error('EXPLORER_WARNING_LOST_RED');
    return {ok:true,root,tree,md};
  }finally{await context.close();}
}

async function uiExplorerIdentityRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();let release=()=>{};
  try{
    const gate=new Promise(resolve=>release=resolve);let held=false;
    await p.route('**/open?*',async route=>{if(new URL(route.request().url()).searchParams.get('mode')!=='source')return route.continue();held=true;await gate;await route.fulfill({json:{path:'D:/wrong/Inserted.pdf',name:'Inserted.pdf',key:'wrong-inserted-source',source_ready:true,pages:[[400,500]]}}).catch(()=>{});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="0"] .filename').click();while(!held)await p.waitForTimeout(10);
    await p.evaluate(()=>window.pnx.on({kind:'queued',files:[{name:'Inserted.pdf',path:window.__fixture.path+'/Inserted.pdf',status:'ready'},...window.__fixture.files]}));release();await p.waitForFunction(()=>!document.querySelector('.source-loading'));
    const result=await p.evaluate(()=>({name:document.querySelector('#reader-name').textContent,wrong:[...document.images].some(n=>n.src.includes('wrong-inserted-source')),body:document.querySelector('#source-scroll').textContent}));
    if(result.name!=='A.pdf'||result.wrong||!result.body.includes('목록이 바뀌'))throw new Error('EXPLORER_WRONG_SOURCE_IDENTITY_RED '+JSON.stringify(result));
    return {ok:true,result};
  }finally{release();await context.close();}
}

async function uiExplorerNavigationToolsRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();
  try{
    const thumbs=[];await p.route('**/thumbnail?*',route=>{thumbs.push(route.request().url());return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="320"><rect width="240" height="320" fill="white"/></svg>'});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    if(!await p.locator('#folder-location [data-folder-path]').count()||!await p.locator('#file-filter').isVisible())throw new Error('EXPLORER_BREADCRUMB_FILTER_RED');
    const checked=await p.locator('[data-check]:checked').count();await p.locator('#file-filter').fill('B.');if(await p.locator('.file-row').count()!==1||!(await p.locator('.file-row .filename').textContent()).includes('B.pdf'))throw new Error('EXPLORER_FILENAME_FILTER_RED');
    await p.locator('#file-filter').fill('');if(await p.locator('[data-check]:checked').count()!==checked)throw new Error('EXPLORER_FILTER_CHECK_LOST_RED');
    await p.locator('[data-action="view-thumbnails"]').click();await p.waitForSelector('.file-thumbnail img');await p.waitForFunction(()=>[...document.querySelectorAll('.file-thumbnail img')].some(n=>n.complete&&n.naturalWidth));
    const mode=await p.evaluate(()=>({tiles:document.querySelector('#rows').classList.contains('thumbnail-rows'),pressed:document.querySelector('[data-action="view-thumbnails"]').getAttribute('aria-pressed')}));if(!mode.tiles||mode.pressed!=='true'||!thumbs.length||thumbs.some(url=>!url.includes('expected_path=')))throw new Error('EXPLORER_THUMBNAIL_IDENTITY_RED '+JSON.stringify({mode,thumbs}));
    await p.locator('[data-action="view-list"]').click();await p.evaluate(()=>window.pnx.on({kind:'folder',...window.__fixture,files:[{name:'Readme.txt',path:window.__fixture.path+'/Readme.txt',status:'ready',extractable:false,md_ready:false}]}));await p.locator('.file-row').click();await p.waitForSelector('#source-scroll');if(!await p.locator('#reader-extract').isDisabled()||!await p.locator('#extract').isDisabled())throw new Error('EXPLORER_TEXT_EXTRACTABLE_RED');
    return {ok:true,mode,thumbs:thumbs.length};
  }finally{await context.close();}
}

async function uiNativeSourceRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage(),revision='a'.repeat(64),requests=[];
  try{
    await p.route('**/open?*',route=>{const u=new URL(route.request().url());return route.fulfill({json:{path:u.searchParams.get('expected_path'),source_kind:'content',source_ready:true,source_revision:revision,pages:[],blocks:[{id:'docx-b000001',kind:'paragraph',text:'<img src=x onerror=alert(1)> literal source'},{id:'docx-b000002',kind:'table',rows:[['A','B'],['source cell','3']]}],start:0,count:2,next_start:2,truncated:true}});});
    await p.route('**/native-preview?*',route=>{const u=new URL(route.request().url());requests.push(u.href);return route.fulfill({json:{path:u.searchParams.get('expected_path'),source_kind:'content',source_ready:true,source_revision:revision,pages:[],blocks:[{id:'docx-b000003',kind:'paragraph',text:'NATIVE_WINDOW_TAIL'}],start:2,count:2,next_start:null,truncated:false}});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="0"] .filename').click();await p.waitForSelector('#native-content');await p.waitForSelector('#native-content [data-native-id="docx-b000003"]');
    const literal=await p.locator('#native-content').textContent();if(!literal.includes('<img src=x')||await p.locator('#native-content img').count()||await p.locator('#native-content table td').count()!==4||requests.length!==1||!requests[0].includes('r='+revision)||!requests[0].includes('expected_path='))throw new Error('NATIVE_CONTENT_WINDOW_RED '+JSON.stringify({literal,requests}));
    await p.locator('#reader-close').click();await p.unroute('**/open?*');await p.unroute('**/native-preview?*');
    await p.route('**/open?*',route=>route.fulfill({json:{path:new URL(route.request().url()).searchParams.get('expected_path'),source_kind:'sheet',source_ready:true,source_revision:revision,pages:[],sheets:[{index:0,name:'첫 시트',state:'visible'},{index:1,name:'Second',state:'visible'}],sheet_index:0,start_row:1,start_col:1,row_count:60,col_count:2,rows:[['Source A1','=A1*2']],formula_cells:[{cell:'B1',formula:'=A1*2',cached_value:42}],has_more:false}}));
    await p.route('**/native-preview?*',route=>route.fulfill({json:{path:new URL(route.request().url()).searchParams.get('expected_path'),source_kind:'sheet',source_ready:true,source_revision:revision,pages:[],sheets:[{index:0,name:'첫 시트',state:'visible'},{index:1,name:'Second',state:'visible'}],sheet_index:1,start_row:1,start_col:1,row_count:60,col_count:2,rows:[['SECOND_SHEET', '42']],has_more:false}}));
    await p.locator('.file-row[data-index="1"] .filename').click();await p.waitForSelector('#native-sheet');if((await p.locator('#native-sheet td').allTextContents()).join()!=='Source A1,42'||!(await p.locator('#native-sheet td').last().getAttribute('title')).includes('=A1*2'))throw new Error('NATIVE_SHEET_FORMULA_LITERAL_RED');
    await p.locator('[data-native-sheet="1"]').click();await p.waitForSelector('#native-sheet:has-text("SECOND_SHEET")');
    return {ok:true,requests,literal,sheet:await p.locator('#native-sheet').textContent()};
  }finally{await context.close();}
}

async function uiExplorerArrowRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();let release=()=>{};
  try{
    const gate=new Promise(resolve=>release=resolve);await p.route('**/open?*',async route=>{const u=new URL(route.request().url());if(u.searchParams.get('p')==='1')await gate;await route.fulfill({json:{path:u.searchParams.get('expected_path'),source_ready:true,pages:[[400,500]],key:'arrow-'+u.searchParams.get('p')}}).catch(()=>{});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="0"]').click();await p.waitForSelector('.pdf-page');const before=await p.locator('[data-check]:checked').count();
    await p.keyboard.press('ArrowDown');await p.keyboard.press('ArrowDown');await p.waitForFunction(()=>document.querySelector('#reader-name').textContent==='C.pdf');await p.waitForFunction(()=>document.querySelector('.pdf-page img')?.src.includes('arrow-2'));release();await p.waitForTimeout(50);
    const result=await p.evaluate(()=>({name:document.querySelector('#reader-name').textContent,image:document.querySelector('.pdf-page img').src,selected:document.querySelector('.file-row.current').dataset.index,focus:document.activeElement.dataset.index,checks:document.querySelectorAll('[data-check]:checked').length}));if(result.name!=='C.pdf'||!result.image.includes('arrow-2')||result.selected!=='2'||result.focus!=='2'||result.checks!==before)throw new Error('EXPLORER_ARROW_LATEST_INTENT_RED '+JSON.stringify(result));return {ok:true,result};
  }finally{release();await context.close();}
}

async function uiViewerShellRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();
  try{
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.evaluate(()=>window.pnx.on({kind:'folder',...window.__fixture,parent:null,folders:[{name:'Child',path:window.__fixture.path+'/Child'}],navigation:{shortcuts:[{name:'문서',path:window.__fixture.path}],drives:[{name:'C:',path:'C:/'}]}}));
    if(await p.locator('[data-check]:checked').count()||await p.locator('.status:visible,.row-gauge:visible').count()||!await p.locator('.file-row .filename svg').count()||!await p.locator('#folder-shortcuts svg').count()||await p.locator('#drive-group').evaluate(n=>n.open))throw new Error('VIEWER_SHELL_DOCUMENT_FIRST_RED');
    if((await p.title())!=='문서 뷰어'||(await p.locator('#extract').textContent()).trim()!=='MD 추출'||!await p.locator('#reader-md').isVisible())throw new Error('VIEWER_SHELL_SECONDARY_MD_MENU_RED');
    await p.locator('.file-row[data-index="1"] .filename').click();await p.waitForSelector('.pdf-page');
    const shell=await p.evaluate(()=>({tree:document.querySelector('#folder-panel').getBoundingClientRect().width,list:document.querySelector('.file-list').getBoundingClientRect().width,paneLabel:getComputedStyle(document.querySelector('#source-pane>.pane-label')).display,source:document.querySelector('#source-scroll').getBoundingClientRect().height,toolbar:document.querySelector('.reader-tools').getBoundingClientRect().height,visibleStatus:[...document.querySelectorAll('.status')].some(n=>n.getBoundingClientRect().height)}));
    if(shell.tree<170||shell.tree>185||shell.list<280||shell.list>310||shell.paneLabel!=='none'||shell.source<500||shell.toolbar>38||shell.visibleStatus)throw new Error('VIEWER_SHELL_GEOMETRY_RED '+JSON.stringify(shell));
    await p.locator('#reader-md').click();await p.waitForSelector('#document h1');if(!await p.locator('#list-view').isVisible()||await p.locator('#source-pane').isVisible())throw new Error('VIEWER_SHELL_MD_SAME_PANE_RED');
    return {ok:true,shell};
  }finally{await context.close();}
}

async function uiMarkdownSourceRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage(),text='# Heading\n\nneedle source text';
  try{
    await p.route('**/open?*',route=>route.fulfill({json:{path:new URL(route.request().url()).searchParams.get('expected_path'),source_kind:'text',source_ready:true,source_revision:'md-v1',format:'md',text,markdown_html:'<h1 data-source-line="1">Heading</h1><p data-source-line="3">needle source text</p>',pages:[]}}));
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.evaluate(()=>{const api=window.pywebview.api;window.pnx.on({kind:'folder',...window.__fixture,files:[{name:'Readme.md',path:window.__fixture.path+'/Readme.md',extractable:false,md_ready:false,status:'ready'}]});window.pywebview.api=new Proxy(api,{get(t,n){if(n==='search_source')return async()=>({source_revision:'md-v1',results:[{side:'source',text:'needle source text',locations:[{kind:'text',line:3,offset:11,length:6}]}]});return t[n];}});});
    await p.locator('.file-row .filename').click();await p.waitForSelector('#source-markdown h1');if(!await p.locator('#extract').isDisabled()||!await p.locator('#reader-md').isEnabled())throw new Error('MARKDOWN_VIEWER_ACTIONS_RED');
    await p.keyboard.press('Control+f');await p.locator('#search').fill('needle');await p.locator('#search-form').evaluate(n=>n.requestSubmit());await p.waitForSelector('#source-markdown [data-source-line="3"].native-location');
    await p.locator('#source-code-toggle').click();await p.waitForSelector('#source-text');if((await p.locator('#source-text').textContent())!==text)throw new Error('MARKDOWN_RAW_SOURCE_CHANGED_RED');
    await p.locator('#reader-md').click();await p.waitForSelector('#source-markdown h1');return {ok:true,raw:text,rendered:await p.locator('#source-markdown').textContent()};
  }finally{await context.close();}
}

async function uiExplorerCurrentTreeRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();
  try{
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.evaluate(()=>{const api=window.pywebview.api,root='D:/Documents',path=root+'/Child/Grandchild';window.__ancestorReads=[];window.pywebview.api=new Proxy(api,{get(t,n){if(n==='list_folders')return async current=>{window.__ancestorReads.push(current);return {ok:true,path:current,folders:current===root?[{name:'Child',path:root+'/Child'}]:current===root+'/Child'?[{name:'Grandchild',path}]:[]};};return t[n];}});window.pnx.on({kind:'folder',path,parent:root+'/Child',folders:[],files:window.__fixture.files,navigation:{shortcuts:[{name:'문서',path:root}],drives:[{name:'C:',path:'C:/'},{name:'D:',path:'D:/'}]}});});
    await p.waitForSelector('#folder-shortcuts [aria-current="true"][data-folder-path$="Grandchild"]');
    const result=await p.evaluate(()=>({reads:window.__ancestorReads,expanded:[...document.querySelectorAll('#folder-shortcuts .tree-toggle[aria-expanded=true]')].map(n=>n.dataset.folderPath),pc:document.querySelector('#drive-group').open}));
    if(result.reads.join()!=='D:/Documents,D:/Documents/Child'||result.expanded.join()!=='D:/Documents,D:/Documents/Child'||result.pc)throw new Error('EXPLORER_CURRENT_ANCESTORS_RED '+JSON.stringify(result));
    await p.locator('#folder-shortcuts .tree-toggle').first().click();if(await p.locator('#folder-shortcuts [data-folder-path$="Grandchild"]').count())throw new Error('EXPLORER_ANCESTOR_MANUAL_COLLAPSE_RED');
    return {ok:true,result};
  }finally{await context.close();}
}

async function uiNativeWindowOwnershipRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage(),revision='b'.repeat(64);let release=()=>{};
  try{
    const gate=new Promise(resolve=>release=resolve);let held=false;
    const sheet=(path,index,text)=>({path,source_kind:'sheet',source_ready:true,source_revision:revision,pages:[],sheets:[{index:0,name:'First',state:'visible'},{index:1,name:'Second',state:'visible'}],sheet_index:index,start_row:1,start_col:1,row_count:60,col_count:1,rows:[[text]],has_more:false});
    await p.route('**/open?*',route=>route.fulfill({json:sheet(new URL(route.request().url()).searchParams.get('expected_path'),0,'FIRST_READY')}));
    await p.route('**/native-preview?*',async route=>{const u=new URL(route.request().url()),index=Number(u.searchParams.get('sheet_index'));if(index===0){held=true;await gate;}await route.fulfill({json:sheet(u.searchParams.get('expected_path'),index,index?'SECOND_READY':'STALE_FIRST')}).catch(()=>{});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="0"]').click();await p.waitForSelector('#native-sheet:has-text("FIRST_READY")');
    await p.locator('[data-native-sheet="0"]').click();while(!held)await p.waitForTimeout(5);await p.locator('[data-native-sheet="1"]').click();await p.waitForSelector('#native-sheet:has-text("SECOND_READY")');release();await p.waitForTimeout(40);
    const result=await p.evaluate(()=>({text:document.querySelector('#native-sheet').textContent,active:document.querySelector('[data-native-sheet][aria-pressed=true]').dataset.nativeSheet}));if(result.text.includes('STALE_FIRST')||!result.text.includes('SECOND_READY')||result.active!=='1')throw new Error('NATIVE_SHEET_LATE_WINDOW_RED '+JSON.stringify(result));return {ok:true,result};
  }finally{release();await context.close();}
}

async function uiPdfHistoryLeaseRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage(),active=[],bad=[];let opened=0;
  try{
    await p.route('**/open?*',route=>{const u=new URL(route.request().url()),key=++opened;active.push(key);if(active.length>4)active.shift();return route.fulfill({json:{path:u.searchParams.get('expected_path'),source_kind:'pdf',source_ready:true,key,pages:Array.from({length:20},()=>[400,500])}});});
    await p.route('**/page?*',route=>{const key=Number(new URL(route.request().url()).searchParams.get('k'));if(!active.includes(key)){bad.push(key);return route.fulfill({status:404,body:'retired PDF key'});}return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="500"><rect width="400" height="500" fill="white"/></svg>'});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.evaluate(()=>window.pnx.on({kind:'folder',...window.__fixture,files:Array.from({length:6},(_,i)=>({name:'History '+i+'.pdf',path:window.__fixture.path+'/History '+i+'.pdf',status:'ready',md_ready:false}))}));
    for(let i=0;i<6;i++){await p.locator('.file-row[data-index="'+i+'"]').click();await p.waitForFunction(()=>document.querySelector('.pdf-page img')?.naturalWidth>0);if(i===0){await p.locator('#source-scroll').evaluate(n=>n.scrollTop=520);await p.waitForTimeout(30);}}
    for(let i=4;i>=0;i--){await p.keyboard.press('Alt+ArrowLeft');await p.waitForFunction(name=>document.querySelector('#reader-name').textContent===name,'History '+i+'.pdf');await p.waitForFunction(()=>document.querySelector('.pdf-page img')?.complete);}
    const scroll=await p.locator('#source-scroll').evaluate(n=>n.scrollTop);await p.locator('#source-scroll').evaluate(n=>n.scrollTop=n.scrollHeight);await p.waitForFunction(()=>document.querySelector('.pdf-page:last-child img')?.complete);const decoded=await p.locator('.pdf-page').last().evaluate(n=>n.querySelector('img').naturalWidth>0);
    if(bad.length||!decoded||scroll<500||opened!==11)throw new Error('PDF_HISTORY_RETIRED_KEY_RED '+JSON.stringify({bad,decoded,scroll,opened}));return {ok:true,bad,decoded,scroll,opened};
  }finally{await context.close();}
}

async function uiPdfRendererHistoryRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage(),requests=[];
  try{
    await p.route('**/open?*',route=>{const u=new URL(route.request().url());requests.push({p:u.searchParams.get('p'),renderer:u.searchParams.get('renderer')});return route.fulfill({json:u.searchParams.get('p')==='1'&&u.searchParams.get('renderer')!=='pdf'?{path:u.searchParams.get('expected_path'),source_kind:'presentation',source_ready:true,pages:[[960,720],[960,720],[960,720]],source_revision:'fixture-source-v1',source_data_url:'/presentation-data?p=1&r=fixture-source-v1',source_frame_url:'/presentation-frame'}:{path:u.searchParams.get('expected_path'),source_kind:'pdf',source_ready:true,key:1,pages:[[400,500],[400,500],[400,500]]}});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="1"]').click();await p.waitForFunction(()=>document.querySelector('#presentation-frame')?.dataset.ready==='true');await p.locator('#source-pdf-fallback').click();await p.waitForSelector('.pdf-page');await p.locator('.file-row[data-index="0"]').click();await p.waitForSelector('.pdf-page');await p.keyboard.press('Alt+ArrowLeft');await p.waitForFunction(()=>document.querySelector('#reader-name').textContent==='B.pdf');await p.waitForSelector('.pdf-page');
    if(await p.locator('#presentation-frame').count()||requests.at(-1).renderer!=='pdf')throw new Error('PDF_RENDERER_HISTORY_RESET_RED '+JSON.stringify(requests));return {ok:true,requests};
  }finally{await context.close();}
}

async function uiHwpNativePagesRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage(),requested=[],revision='c'.repeat(64);let maps=0;
  try{
    await p.route('**/open?*',route=>{const u=new URL(route.request().url());return route.fulfill({json:u.searchParams.get('mode')==='result'?{path:u.searchParams.get('expected_path'),revision:'saved-B-v1',doc:{extracted:true,html:'<h1>HWP saved result</h1><p data-block-id="p1">needle saved paragraph</p>',md_text:'needle saved paragraph'}}:{path:u.searchParams.get('expected_path'),source_kind:'hwp_native',source_ready:true,source_revision:revision,source_page_url:'/hwp-page?p=1&expected_path='+encodeURIComponent(u.searchParams.get('expected_path'))+'&r='+revision,pages:Array.from({length:20},()=>[794,1123])}});});
    await p.route('**/hwp-page?*',route=>{requested.push(new URL(route.request().url()).searchParams.get('i'));return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="794" height="1123"><rect width="794" height="1123" fill="white"/></svg>'});});
    await p.route('**/document-map?*',route=>{maps++;return route.fulfill({status:500,json:{error:'HWP must not convert a PDF map'}});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.evaluate(revision=>{const api=window.pywebview.api;window.pywebview.api=new Proxy(api,{get(t,n){if(n==='search_source')return async()=>({source_revision:revision,results:[{side:'source',text:'needle page',locations:[{kind:'hwp_native',page:17}]}]});return t[n];}});},revision);
    await p.locator('.file-row[data-index="1"]').click();await p.waitForFunction(()=>document.querySelector('.pdf-page img')?.naturalWidth>0);if(requested.length>5||!requested.length)throw new Error('HWP_ALL_PAGES_EAGER_RED '+JSON.stringify(requested));
    const urls=await p.locator('.pdf-page img').evaluateAll(nodes=>nodes.map(n=>n.src));if(urls.some(url=>!url.includes('viewer=')||!url.includes('generation=')||!url.includes('expected_path=')||!url.includes('r='+revision)))throw new Error('HWP_PAGE_IDENTITY_MISSING_RED');
    await p.keyboard.press('Control+f');await p.locator('#search').fill('needle');await p.locator('#search-form').evaluate(n=>n.requestSubmit());await p.waitForFunction(()=>document.querySelector('#source-position').textContent.startsWith('18 /'));await p.locator('#reader-md').click();await p.waitForSelector('#document h1');await p.locator('#document [data-block-id="p1"]').click();if(maps)throw new Error('HWP_PDF_MAP_CONVERSION_RED');return {ok:true,requested,maps,urls:urls.slice(0,2)};
  }finally{await context.close();}
}

async function uiSourceWarningsDisclosureRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();
  try{
    await p.route('**/open?*',route=>route.fulfill({json:{path:new URL(route.request().url()).searchParams.get('expected_path'),source_kind:'content',source_ready:true,source_revision:'native-warning-v1',format:'docx',pages:[],blocks:[{id:'b1',kind:'paragraph',text:'Native source'}],next_start:null,partial:true,warnings:['원본의 글상자는 내용 미리보기에 표시되지 않습니다.']}}));
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="0"]').click();await p.waitForSelector('#native-content');if((await p.locator('#native-content').textContent()).includes('글상자'))throw new Error('SOURCE_WARNINGS_DEFAULT_NOISE_RED');
    await p.locator('#reader-name').click();await p.waitForSelector('#file-dialog[open]');const details=p.locator('#properties .source-notice-details');if(await details.count()!==1||await details.evaluate(n=>n.open))throw new Error('SOURCE_WARNINGS_NOT_DISCOVERABLE_RED');
    await details.locator('summary').click();if(!(await details.textContent()).includes('글상자'))throw new Error('SOURCE_WARNING_LOST_RED');await p.locator('[data-action="file-close"]').click();
    await p.locator('.file-row[data-index="1"] .info-button').click();await p.waitForSelector('#file-dialog[open]');if(await p.locator('#properties .source-notice-details').count())throw new Error('SOURCE_WARNINGS_WRONG_DOCUMENT_RED');return {ok:true};
  }finally{await context.close();}
}

async function uiNativeWindowWarningsRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1040,height:760}}),p=await context.newPage();
  try{
    const content=(path,tail)=>({path,source_kind:'content',source_ready:true,source_revision:'native-tail-warnings-v1',format:'docx',pages:[],blocks:[{id:tail?'tail':'head',kind:'paragraph',text:tail?'Native tail':'Native head'}],next_start:tail?null:1,warnings:tail?['뒤쪽 창의 글상자가 생략됐습니다.']:[]});
    await p.route('**/open?*',route=>route.fulfill({json:content(new URL(route.request().url()).searchParams.get('expected_path'),false)}));await p.route('**/native-preview?*',route=>route.fulfill({json:content(new URL(route.request().url()).searchParams.get('expected_path'),true)}));
    await p.goto(baseURL);await p.waitForSelector('.file-row');await p.locator('.file-row[data-index="0"]').click();await p.waitForSelector('#native-content:has-text("Native tail")');await p.locator('#reader-name').click();const details=p.locator('#properties .source-notice-details');if(await details.count()!==1||!(await details.textContent()).includes('뒤쪽 창의 글상자'))throw new Error('NATIVE_WINDOW_WARNING_LOST_RED');if(await details.evaluate(n=>n.open))throw new Error('NATIVE_WINDOW_WARNING_DEFAULT_OPEN_RED');return {ok:true};
  }finally{await context.close();}
}
