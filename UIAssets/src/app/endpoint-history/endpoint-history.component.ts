import { Component, OnInit } from '@angular/core';
import { PreferencesService } from '../_service/preferences.service';
import { IfStmt } from '@angular/compiler';
import { BackendService } from '../_service/backend.service';
import { ActivatedRoute } from '@angular/router';

@Component({
  selector: 'app-endpoint-history',
  templateUrl: './endpoint-history.component.html',
  styleUrls: ['./endpoint-history.component.css']
})
export class EndpointHistoryComponent implements OnInit {
  tabs:any ;
  endpoint:any ;
  endpointStatus='';
  fabricDetails='';
  staleoffsubnetDetails = '' ;
  showModal = false;
  modalTitle = '' ;
  modalBody = '' ;
  fab:string ;
  vnid:string;
  address:string;
  clearEndpointOptions:any ;
  clearNodes = [] ;
  showClearModal = false ;

  constructor(private prefs:PreferencesService, private bs : BackendService, private activatedRoute:ActivatedRoute) {
    this.tabs = [
      {name:' Local Learns',icon:'icon-clock',path:'pernodehistory'},
      {name: 'Move Events', path:'moveevents', icon:'icon-panel-shift-right'},
      {name:' Off-Subnet Events',path:'offsubnetevents', icon:'icon-jump-out'},
      {name:' Stale Events',path:'staleevents',icon:'icon-warning'}
    ] ;
    this.endpoint = this.prefs.endpointDetailsObject ;
    
    if(this.endpoint === undefined || this.endpoint === null) {
      this.fab = this.activatedRoute.snapshot.params.fabric;
      this.vnid = this.activatedRoute.snapshot.params.vnid ;
      this.address = this.activatedRoute.snapshot.params.address;
      this.getSingleEndpoint(this.fab,this.vnid,this.address) ;
    }else{
      this.setupStatusAndInfoStrings() ;
      this.prefs.selectedEndpoint = this.endpoint ;
    }

    this.clearEndpointOptions = [
      {label:'Select all' , value:0},
      {label:'Offsubnet endpoints', value:1},
      {label:'Stale Endpoints',value:2}
    ]
    
  }

  ngOnInit() {
  }

  setupStatusAndInfoStrings() {
    const status = this.endpoint.events.length > 0 ? this.endpoint.events[0].status : this.endpoint.first_learn.status ;
    const node = this.endpoint.events.length > 0 ? this.endpoint.events[0].node : this.endpoint.first_learn.node ;
    const intf = this.endpoint.events.length > 0 ? this.endpoint.events[0].intf_name : this.endpoint.first_learn.intf_name ;
    const encap = this.endpoint.events.length > 0 ? this.endpoint.events[0].encap : this.endpoint.first_learn.encap ;
    const epgname = this.endpoint.events.length > 0 ? this.endpoint.events[0].epg_name : this.endpoint.first_learn.epg_name ;
    const vrfbd = this.endpoint.events.length > 0 ? this.endpoint.events[0].vnid_name : this.endpoint.first_learn.vnid_name ;
    if(this.endpoint.is_offsubnet) {
      this.staleoffsubnetDetails += 'Currently offsubnet on node ' + node + '\n' ;
    }
    if(this.endpoint.is_stale) {
      this.staleoffsubnetDetails += 'Current stale on node ' + node  ;
    }

    if(status  === 'deleted') {
      this.endpointStatus = 'Not currently present in the fabric' ;
    }else {
      this.endpointStatus = 'Local on node ' + node + ', interface ' + intf 
      if(encap !== '') {
        this.endpointStatus += ', encap ' + encap ;
      }
      //rw_mac logic and vpc decoding here
      if(epgname !== '') {
        this.endpointStatus += ', epg ' + epgname
      }
    }

    this.fabricDetails = 'Fabric ' + this.endpoint.fabric ;
    if(this.endpoint.type === 'ipv4' || this.endpoint.type==='ipv6') {
      this.fabricDetails += ', VRF ' 
    }else{
      this.fabricDetails += ', BD ' ;
    }
    this.fabricDetails += vrfbd + ', VNID ' + this.endpoint.vnid ;
  }

  onClickOfDelete() {
    this.modalTitle = 'Warning' ;
    this.modalBody = 'Are you sure you want to delete all information for ' + this.endpoint.addr + ' from the local database? Note, this will not affect the endpoint state within the fabric.'
    this.showModal = true ;
  }

  deleteEndpoint() {
    this.showModal = false ;
    this.bs.deleteEndpoint(this.endpoint.addr).subscribe(
      (data) => {

      },
    (error) => {

    }
    )

  }

  getSingleEndpoint(fabric,vnid,address) {
    this.bs.getSingleEndpoint(fabric,vnid,address).subscribe(
      (data)=>{
        this.endpoint = data['objects'][0]['ept.endpoint'] ;
        this.prefs.selectedEndpoint = this.endpoint;
        this.setupStatusAndInfoStrings() ;
      },
      (error)=>{
        console.log(error) ;
      }
    ) ;
  }

  refresh() {
    this.getSingleEndpoint(this.endpoint.fabric , this.endpoint.vnid, this.endpoint.addr) ;
  }

  addNodes = (term) => {
    return {label: term, value: term};
  }


  public filterNodes(nodes): any[] {
    let newarr: any[] = [];
    if (nodes !== undefined) {
      for (let i = 0; i < nodes.length; i++) {
        if (typeof(nodes[i]) === 'string') {
          if (nodes[i] !== 'global') {
            if (nodes[i].includes(',')) {
              nodes[i] = nodes[i].replace(/\s/g, '');
              const csv = nodes[i].split(',');
              for (let j = 0; j < csv.length; j++) {
                if (csv[j].includes('-')) {
                  newarr = newarr.concat(this.getArrayForRange(csv[j]));
                }
              }
            } else if (nodes[i].includes('-')) {
              newarr = newarr.concat(this.getArrayForRange(nodes[i]));
            } else {
              newarr.push(nodes[i]);
            }
          } else {
            newarr.push(0);
          }
        }
      }
    }
    return newarr;
  }

  public getArrayForRange(range: string) {
    const r = range.split('-');
    const arr = [];
    r.sort();
    for (let i = parseInt(r[0], 10); i <= parseInt(r[1], 10); i++) {
      arr.push(i);
    }
    return arr;
  }

  public clearEndpoints() {
    
  }



}