/* Shared Phaser 3 room renderer: all visual content comes from the atlas and layout. */
class SpriteForgePreview extends Phaser.Scene {
  preload() {
    this.load.on('loaderror', file => window.previewErrors.push('Could not load ' + file.key));
    this.load.atlas('blocks', 'atlas.png', 'atlas.json');
    this.load.json('room', 'layout.json');
    this.load.json('style', 'style.json');
    this.load.bitmapFont('pixel', 'font.png', 'runtime/font.xml');
  }
  create() {
    const room=this.cache.json.get('room'), palette=this.cache.json.get('style').palette;
    const color=i=>parseInt(palette[i].slice(1),16);
    this.cameras.main.setBackgroundColor(color(1));
    const iso=room.perspective==='isometric';
    if(iso){
      // Integer-pixel inverse projection preserves the palette and crisp tile edges.
      for(const id of new Set(room.tiles.flat().filter(id=>id!=='empty'))){
        const frame=this.textures.getFrame('blocks',id);
        const source=document.createElement('canvas');source.width=16;source.height=16;
        const context=source.getContext('2d',{willReadFrequently:true});
        context.drawImage(frame.source.image,frame.cutX,frame.cutY,16,16,0,0,16,16);
        const input=context.getImageData(0,0,16,16).data;
        const texture=this.textures.createCanvas('iso-'+id,16,8), ctx=texture.context;
        const output=ctx.createImageData(16,8);
        for(let y=0;y<8;y++)for(let x=0;x<16;x++){
          const u=Math.floor((x-7.5)+2*(y+0.5)),v=Math.floor(2*(y+0.5)-(x-7.5));
          if(u>=0&&u<16&&v>=0&&v<16)output.data.set(input.slice((v*16+u)*4,(v*16+u)*4+4),(y*16+x)*4);
        }
        ctx.putImageData(output,0,0);texture.refresh();
      }
    }
    for(let row=0;row<11;row++)for(let col=0;col<20;col++){
      const id=room.tiles[row][col];if(id==='empty')continue;
      if(iso)this.add.image(88+(col-row)*8,36+(col+row)*4,'iso-'+id).setOrigin(.5,0);
      else this.add.image(col*16,row*16,'blocks',id).setOrigin(0);
    }
    for(const entity of [...room.entities].sort((a,b)=>a.y-b.y))
      this.add.image(entity.x,entity.y,'blocks',entity.asset).setOrigin(.5,1);
    const hud=this.add.graphics();hud.fillStyle(color(0),1);hud.fillRect(0,0,320,16);
    hud.fillStyle(color(palette.length-1),1);
    const heart=['0110110','1111111','1111111','0111110','0011100','0001000'];
    for(let i=0;i<room.hud.hearts;i++)heart.forEach((row,y)=>[...row].forEach((p,x)=>{
      if(p==='1')hud.fillRect(7+i*10+x,5+y,1,1);
    }));
    this.add.bitmapText(68,5,'pixel','KEYS '+room.hud.pickup_count,6);
    this.add.bitmapText(170,5,'pixel',room.hud.weapon.toUpperCase(),6);
    this.game.events.once('postrender',()=>{window.previewReady=true;});
  }
}
