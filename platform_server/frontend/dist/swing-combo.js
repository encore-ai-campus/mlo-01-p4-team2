(function(root){
  class SwingCombo {
    constructor(){this.remaining=10;this.active=false;this.elapsed=0;this.stroke=0;this.resolved=false;this.duration=500;this.lastResult='ready';}
    start(){if(this.remaining>0)this.active=true;}
    stop(){this.active=false;this.elapsed=0;this.resolved=false;}
    refill(){this.stop();this.remaining=10;this.stroke=0;this.lastResult='refill';}
    advance(ms,outcome='hit'){
      if(!this.active||!Number.isFinite(ms)||ms<=0)return;
      this.elapsed+=Math.min(ms,50);
      if(!this.resolved&&this.elapsed>=this.duration*.56){
        this.resolved=true;this.lastResult=outcome;
        if(outcome==='hit')this.remaining=Math.max(0,this.remaining-1);
        else if(outcome==='block')this.remaining=0;
      }
      if(this.elapsed>=this.duration){this.elapsed-=this.duration;this.stroke++;this.resolved=false;if(!this.remaining)this.stop();}
    }
    get phase(){return this.active?this.elapsed/this.duration:0;}
    get frame(){return this.active?(this.stroke%2)*4+Math.min(3,Math.floor(this.phase*4)):0;}
  }
  if(typeof module==='object')module.exports=SwingCombo;else root.SwingCombo=SwingCombo;
})(typeof window==='object'?window:globalThis);
