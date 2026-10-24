async function uiFoldersRegression(page,baseURL){
  const context=await page.context().browser().newContext({viewport:{width:1336,height:900}}),p=await context.newPage();
  try{
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    if(await p.locator('#folder-address,[data-action="add-folder"]').count()||!await p.locator('#folder-panel').count())throw new Error('UI_FOLDERS_SIDEBAR_RED');
    if((await p.locator('#open-files').textContent())!=='열기…')throw new Error('UI_FOLDERS_OPEN_LABEL_RED');
    await p.evaluate(()=>{
      const original=window.pywebview.api,root=window.__fixture.path;
      window.__folderVisits=[];window.__pickerCalls=0;
      const state=path=>({ok:true,path,parent:path===root?null:root,folders:path===root?[{name:'Child',path:root+'/Child'}]:[],files:path===root?window.__fixture.files:[{name:'Nested.pdf',path:path+'/Nested.pdf',status:'ready',md_ready:false}],output_dir:window.__fixture.output_dir,navigation:{shortcuts:[{name:'문서',path:root},{name:'Missing',path:root+'/missing'}],drives:[{name:'Fixture drive',path:root}]}});
      window.pywebview.api=new Proxy(original,{get(target,name){if(name==='browse_folder')return async path=>{window.__folderVisits.push(path);return path.endsWith('missing')?{ok:false,why:'폴더를 찾을 수 없습니다.'}:state(path);};if(name==='pick_files')return async()=>{window.__pickerCalls++;return {...state(root),selected_paths:[window.__fixture.files[1].path],open_path:window.__fixture.files[1].path};};return target[name];}});
      window.pnx.on({kind:'folder',...state(root)});
    });
    await p.locator('#folder-children [data-folder-path]').click();await p.waitForSelector('.file-row:has-text("Nested.pdf")');
    await p.locator('#folder-back').click();await p.waitForSelector('#folder-children [data-folder-path]');
    await p.locator('#folder-forward').click();await p.waitForSelector('.file-row:has-text("Nested.pdf")');
    await p.locator('#folder-up').click();await p.waitForSelector('#folder-children [data-folder-path]');
    await p.evaluate(()=>window.pnx.on({kind:'run',on:true}));
    await p.locator('#folder-children [data-folder-path]').click();await p.waitForSelector('.file-row:has-text("Nested.pdf")');
    await p.locator('#folder-shortcuts [data-folder-path]').filter({hasText:'Missing'}).click();
    await p.waitForFunction(()=>document.querySelector('#message').textContent.includes('찾을 수'));
    const result=await p.evaluate(()=>({visits:window.__folderVisits,name:document.querySelector('#folder-name').textContent,files:[...document.querySelectorAll('.filename')].map(n=>n.textContent),stop:!document.querySelector('#stop').disabled,panelWidth:document.querySelector('#folder-panel').getBoundingClientRect().width,listLeft:document.querySelector('.file-list').getBoundingClientRect().left}));
    if(result.name!=='Child'||!result.files.includes('Nested.pdf')||!result.stop||result.visits.length!==6||result.panelWidth<140||result.panelWidth>240||result.listLeft<result.panelWidth)throw new Error('UI_FOLDERS_HISTORY_OR_FAILURE_RED '+JSON.stringify(result));
    await p.evaluate(()=>window.pnx.on({kind:'run',on:false}));await p.keyboard.press('Control+o');await p.waitForSelector('#source-scroll .pdf-page');
    const opened=await p.evaluate(()=>({pickerCalls:window.__pickerCalls,name:document.querySelector('#reader-name').textContent,source:document.querySelector('#reader').classList.contains('source-only'),checks:[...document.querySelectorAll('[data-check]')].map(n=>n.checked),sourceLeft:document.querySelector('#source-pane').getBoundingClientRect().left,panelHidden:document.querySelector('#list-view').hidden}));
    if(opened.pickerCalls!==1||opened.name!=='B.pdf'||!opened.source||JSON.stringify(opened.checks)!=='[false,true,false]'||opened.sourceLeft!==0||!opened.panelHidden)throw new Error('UI_FOLDERS_CTRL_O_OPEN_RED '+JSON.stringify(opened));
    await p.locator('#reader-back').click();await p.setViewportSize({width:720,height:520});await p.waitForFunction(()=>document.querySelector('#folder-panel').hidden);await p.locator('#folder-toggle').click();if(!await p.locator('#folder-panel').isVisible())throw new Error('UI_FOLDERS_EXPLICIT_EXPAND_RED');await p.locator('#folder-toggle').click();if(await p.locator('#folder-panel').isVisible())throw new Error('UI_FOLDERS_EXPLICIT_COLLAPSE_RED');const narrow=await p.locator('.file-list').boundingBox();if(narrow.x!==0||narrow.width<700)throw new Error('UI_FOLDERS_NARROW_LIST_RED '+JSON.stringify(narrow));
    return{ok:true,result,opened,narrow};
  }finally{await context.close();}
}
