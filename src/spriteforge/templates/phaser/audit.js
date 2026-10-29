/* Inspect the real Phaser scene and canvas; this does not render a second preview. */
() => {
  const game=window.previewGame, scene=game.scene.getScenes(true)[0], camera=scene.cameras.main;
  if(!game.config.pixelArt || !camera.roundPixels) throw Error('Pixel rendering must be enabled');
  if(camera.zoomX!==1 || camera.zoomY!==1 || camera.rotation!==0)
    throw Error('Preview camera must use scale 1 without rotation');
  const objects=[];
  const visit=list=>list.forEach(obj=>{
    if(obj.type==='Image' || obj.type==='Sprite') objects.push(obj);
    if(obj.type==='Container') visit(obj.list);
  });
  visit(scene.children.list);
  const sprites=objects.map(obj=>{
    const matrix=obj.getWorldTransformMatrix(), sx=Math.hypot(matrix.a,matrix.b), sy=Math.hypot(matrix.c,matrix.d);
    if(!Number.isInteger(sx) || !Number.isInteger(sy))
      throw Error('Non-integer sprite scale: '+obj.frame.name+' '+sx+','+sy);
    if(sx!==1 || sy!==1 || matrix.b!==0 || matrix.c!==0 || matrix.a!==1 || matrix.d!==1)
      throw Error('Preview sprites must draw at scale 1 without rotation: '+obj.frame.name);
    return {asset:obj.frame.name,scale_x:sx,scale_y:sy};
  });
  const hero=objects.find(obj=>obj.texture.key==='blocks' && obj.frame.name==='hero');
  if(!hero) throw Error('Missing hero');
  const frame=hero.frame, matrix=hero.getWorldTransformMatrix();
  const left=matrix.tx-hero.displayOriginX-camera.scrollX, top=matrix.ty-hero.displayOriginY-camera.scrollY;
  if(!Number.isInteger(left) || !Number.isInteger(top)) throw Error('Hero must align to screen pixels');
  const source=document.createElement('canvas');source.width=frame.cutWidth;source.height=frame.cutHeight;
  const ctx=source.getContext('2d',{willReadFrequently:true});
  ctx.drawImage(frame.source.image,frame.cutX,frame.cutY,frame.cutWidth,frame.cutHeight,0,0,source.width,source.height);
  const pixels=ctx.getImageData(0,0,source.width,source.height).data;
  const screen=game.canvas.getContext('2d').getImageData(0,0,320,180).data;
  let minX=320,minY=180,maxX=-1,maxY=-1,logicalMin=source.height,logicalMax=-1,count=0;
  for(let y=0;y<source.height;y++)for(let x=0;x<source.width;x++){
    const i=4*(y*source.width+x);if(pixels[i+3]!==255)continue;
    logicalMin=Math.min(logicalMin,y);logicalMax=Math.max(logicalMax,y);
    const px=left+x,py=top+y;if(px<0 || px>=320 || py<0 || py>=180)continue;
    const j=4*(py*320+px);
    // Only source-opaque pixels present in the actual screenshot count.
    if([0,1,2].some(c=>screen[j+c]!==pixels[i+c]))continue;
    count++;minX=Math.min(minX,px);maxX=Math.max(maxX,px);minY=Math.min(minY,py);maxY=Math.max(maxY,py);
  }
  return {renderer:'Phaser 3.90.0 / Canvas',screen:[320,180],pixel_art:true,round_pixels:true,
    sprites,hero:{canvas:[source.width,source.height],logical_height:logicalMax-logicalMin+1,
      on_screen_height:count?maxY-minY+1:0,opaque_screen_bounds:count?[minX,minY,maxX+1,maxY+1]:null,
      visible_opaque_pixels:count,scale:1}};
}
