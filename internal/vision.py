"""Recognize menu elements and the local gift icon; never compare whole scenes."""
import json
from pathlib import Path
import cv2
import numpy as np
from paths import RESOURCE_ROOT as PROJECT_ROOT

BASE_SIZE=(1920,1080)
# Shared by every image/template acceptance check, including F-key labels.
SIMILARITY_THRESHOLD=0.65
# Gift1 backpack layout, measured in the 1920x1080 normalized frame.
# Ball slots form a 6-column grid (two visible rows); quantity plus/minus
# buttons sit beside the count slider. All offsets follow the matched
# "give" button anchor, so a slightly shifted UI is still tracked.
GIFT1_GIVE_CENTER=(1540.5,1009.0)
GIFT1_BALL_COLUMNS=(314,462,610,758,906,1054)
GIFT1_BALL_ROWS=(204,352)
GIFT1_PLUS=(1752,928)
GIFT1_MINUS=(1310,928)


class Detector:
    def __init__(self):
        self.templates={}
        metadata=json.loads((PROJECT_ROOT/'assets/templates.json').read_text(encoding='utf-8'))
        for state,anchors in metadata['states'].items():
            self.templates[state]=[(a['name'],a['rect'],cv2.imdecode(np.fromfile(str(PROJECT_ROOT/'assets'/a['file']),np.uint8),0)) for a in anchors]
        self.hotkeys={key:cv2.imdecode(np.fromfile(str(PROJECT_ROOT/'assets'/file),np.uint8),0)
                      for key,file in json.loads((PROJECT_ROOT/'assets/hotkeys.json').read_text(encoding='utf-8')).items()}

    def _gift_action(self,frame,gray):
        """Find the action's name, then read the keycap directly below it."""
        _,reference,label=next(a for a in self.templates['prepare2'] if a[0]=='gifting')
        # The label establishes the position. Never assume a menu slot or ROI.
        matches=cv2.matchTemplate(gray,label,cv2.TM_CCOEFF_NORMED)
        candidates=[]
        for _ in range(3):
            _,score,_,point=cv2.minMaxLoc(matches)
            if not np.isfinite(score) or score<=SIMILARITY_THRESHOLD: break
            x,y=point
            box=(x,y,label.shape[1],label.shape[0])
            if self._lit(gray,box,label,fraction=.60): candidates.append((score,box))
            # Suppress the same label, without suppressing an adjacent slot.
            matches[max(0,y-30):y+31,max(0,x-65):x+66]=-1
        if len(candidates)!=1: return {'gift_key':None,'gift_matches':len(candidates)}
        label_score,box=candidates[0]
        x,y,w,h=box
        dx,dy=x-reference[0],y-reference[1]
        icons=[]
        for group in ('giving','idle_icon'):
            _,rect,template=self.templates[group][0]
            shifted=[rect[0]+dx,rect[1]+dy,rect[2]+dx,rect[3]+dy]
            score,icon_box=self._match(gray,shifted,template,margin=8)
            icons.append((score,icon_box))
        icon_score,icon_box=max(icons,key=lambda item:item[0])
        color,metrics=self.icon_color(frame,icon_box)
        key_scores=[]
        for key,template in self.hotkeys.items():
            kh,kw=template.shape
            cx=x+w/2
            cy=y+h+16
            rect=[int(cx-kw/2),int(cy-kh/2),int(cx+kw/2),int(cy+kh/2)]
            score,key_box=self._match(gray,rect,template,margin=3)
            key_scores.append((score,key,self._lit(gray,key_box,template)))
        key_scores.sort(reverse=True)
        score,key,lit=key_scores[0]
        # Never infer a key from column number or fall back to F2.
        recognized=key if lit and score>SIMILARITY_THRESHOLD and score-key_scores[1][0]>=.045 else None
        return {'gift_key':recognized,'gift_matches':1,'gift_label_score':label_score,
                'gift_label_box':list(box),'gift_icon_score':icon_score,'gift_icon_box':list(icon_box),
                'gift_key_scores':{key:score for score,key,_ in key_scores},
                'icon_color':color,'icon_metrics':metrics}

    @staticmethod
    def normalize(frame):
        if not isinstance(frame,np.ndarray) or frame.ndim!=3 or frame.dtype!=np.uint8 or frame.shape[2] not in (3,4):
            return None
        h,w=frame.shape[:2]
        if min(h,w)<360 or abs(w/h/(16/9)-1)>.025:
            return None
        return cv2.resize(frame[:,:,:3],BASE_SIZE,interpolation=cv2.INTER_AREA if w>1920 else cv2.INTER_LINEAR)

    def _match(self,gray,rect,template,margin=18):
        x1,y1,x2,y2=rect
        left,top=min(1920,max(0,x1-margin)),min(1080,max(0,y1-margin))
        roi=gray[top:max(0,min(1080,y2+margin)),left:max(0,min(1920,x2+margin))]
        if roi.shape[0]<template.shape[0] or roi.shape[1]<template.shape[1]:
            return 0.,(left,top,template.shape[1],template.shape[0])
        _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(roi,template,cv2.TM_CCOEFF_NORMED))
        return float(score) if np.isfinite(score) else 0.,(point[0]+left,point[1]+top,template.shape[1],template.shape[0])

    @staticmethod
    def _lit(gray,box,template,fraction=.75):
        # NCC also matches a dimmed underlying page. Its pale UI pixels must
        # still be bright before that page can be considered actionable.
        x,y,w,h=box
        mask=template>190
        pixels=gray[y:y+h,x:x+w]
        return bool(pixels.shape==mask.shape and mask.any() and np.mean(pixels[mask]>175)>fraction)

    @staticmethod
    def icon_color(frame,box):
        # Analyze only a ring inside this icon's circle. Other players' orange
        # pets/effects must not determine its state. Mixed/occluded is unknown.
        x,y,w,h=box
        cx,cy=x+w/2,y+h/2
        left,top=int(cx-41),int(cy-41)
        roi=frame[max(0,top):int(cy+42),max(0,left):int(cx+42)]
        if roi.shape[:2]!=(83,83): return 'unknown',{}
        hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
        yy,xx=np.ogrid[:83,:83]
        r=(xx-41)**2+(yy-41)**2
        ring=(r>=29**2)&(r<=36**2)
        orange=(hsv[:,:,0]>=9)&(hsv[:,:,0]<=38)&(hsv[:,:,1]>=95)&(hsv[:,:,2]>=160)
        dark=hsv[:,:,2]<100
        warm=float(np.mean(orange[ring])); black=float(np.mean(dark[ring]))
        color='orange' if warm>=.75 and black<.15 else 'normal' if black>=.80 and warm<.10 else 'unknown'
        return color,{'orange_fraction':warm,'dark_fraction':black}

    def _gift1_layout(self,result,give_cx,give_cy):
        """Emit ball-slot, plus and minus click points in the 2048x1152 frame."""
        dx,dy=give_cx-GIFT1_GIVE_CENTER[0],give_cy-GIFT1_GIVE_CENTER[1]
        scale_x,scale_y=2048/1920,1152/1080
        slots=[[(cx+dx)*scale_x,(ry+dy)*scale_y]
               for ry in GIFT1_BALL_ROWS for cx in GIFT1_BALL_COLUMNS]
        result['buttons']['ball_slots']=slots
        result['buttons']['plus']=[(GIFT1_PLUS[0]+dx)*scale_x,(GIFT1_PLUS[1]+dy)*scale_y]
        result['buttons']['minus']=[(GIFT1_MINUS[0]+dx)*scale_x,(GIFT1_MINUS[1]+dy)*scale_y]

    def analyze(self,frame):
        result={'state':'unknown','scores':{},'anchors':{},'buttons':{},'icon_color':'unknown','gift_key':None,
                'similarity_threshold':SIMILARITY_THRESHOLD}
        frame=self.normalize(frame)
        if frame is None: return result
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        if gray.std()<2: return result
        boxes={}
        lit={}
        for state,anchors in self.templates.items():
            if state in {'giving','idle_icon'}: continue
            result['anchors'][state]={}
            for name,rect,template in anchors:
                if state=='prepare2' and name!='actions': continue
                score,box=self._match(gray,rect,template)
                result['anchors'][state][name]=score
                boxes[state,name]=box
                lit[state,name]=self._lit(gray,box,template)
            result['scores'][state]=min(result['anchors'][state].values())
        scores=result['scores']
        # Modal first: the dimmed game/backpack under a modal is not actionable.
        if scores['gift2']>SIMILARITY_THRESHOLD and lit['gift2','confirm']:
            result['state']='gift2'
            target='gift2','confirm'
        elif (scores['gift1']>SIMILARITY_THRESHOLD and scores['gift2']<=SIMILARITY_THRESHOLD and lit['gift1','give']
              and max(result['anchors']['gift2'].values())<=SIMILARITY_THRESHOLD):
            result['state']='gift1'
            target='gift1','give'
        else:
            target=None
        if target:
            x,y,w,h=boxes[target]
            cx,cy=x+w/2,y+h/2
            result['buttons'][target[1]]=[cx*2048/1920,cy*1152/1080]
            if target==('gift1','give'):
                self._gift1_layout(result,cx,cy)
            return result
        if max(scores['gift1'],scores['gift2'])>SIMILARITY_THRESHOLD:
            return result
        menu_visible=result['anchors']['menu']['esc']>SIMILARITY_THRESHOLD and lit['menu','esc']
        if menu_visible:
            # Only the selected category is fixed; individual actions can move.
            actions=result['anchors']['prepare2']['actions']
            if scores['prepare1']>SIMILARITY_THRESHOLD:
                result['state']='prepare1'
            elif actions>SIMILARITY_THRESHOLD and actions-scores['prepare1']>.07:
                result.update(self._gift_action(frame,gray))
                if result.get('gift_icon_score',0)>SIMILARITY_THRESHOLD and result['gift_key']:
                    result['state']={'orange':'giving','normal':'prepare2'}.get(result['icon_color'],'unknown')
            return result
        # Main HUD icons confirm an unready game; missing menus alone cannot.
        if scores['closed']>SIMILARITY_THRESHOLD and lit['closed','main_f3'] and lit['closed','main_f4']:
            result['state']='unready'
        return result
