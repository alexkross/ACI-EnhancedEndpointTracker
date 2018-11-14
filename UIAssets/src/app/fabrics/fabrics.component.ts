import { Component, OnInit,ViewChild, OnDestroy, AfterViewInit, OnChanges } from '@angular/core';
import { BackendService } from '../_service/backend.service';
import { Router } from '@angular/router';
import {Observable,of} from 'rxjs' ;
import {debounceTime, distinctUntilChanged, map, switchMap, mergeMap} from 'rxjs/operators';
import { EventEmitter } from 'events';


@Component({
  selector: 'app-fabrics',
  templateUrl: './fabrics.component.html',
  styleUrls: ['./fabrics.component.css']
})
export class FabricsComponent implements OnInit, OnDestroy{

  
  title = 'app';
  sorts:any ;
  rows:any ;
  tabs:any ;
  tabIndex = 0 ;
  expanded:any = {} ;
  events:any ;
  eventRows:any ;
  latestEvents:any ;
  endpointMoves:any ;
  subnetPoints:any ;
  staleEndpoints:any ;
  showModal:boolean ;
  modalTitle:string ;
  modalBody:string;
  placeholder = "Search MAC or IP address (Ex: 00:50:56:01:11:12, 10.1.1.101, or 2001:a::65)" ;
  searchKey='' ;
  eventObservable:Observable<any> ;

  constructor(public bs: BackendService, private router: Router){
    this.sorts = { name:"fabric", dir:'asc'} ;
    this.rows = [{fabric:'Fabric1' , status:'Stopped', ips:'2300', macs:'2000', 
                 events:[{time:new Date() , status:'Initializing', description:'Connecting to APIC'},{time:new Date(), status:'Restarting' , description:'User triggered restart'}]}]
    this.tabs = [
    {name:'Fabrics',path:'fabric-overview'},
    {name:'Endpoints',path:'endpoints'},
    {name:'Latest Events',path:'latest-events'},
    {name:'Moves',path:'moves'},
    {name:'offSubnet Endpoints',path:'offsubnet-endpoints'},
    {name:'Stale Endpoints', path:'stale-endpoints'}
    ] ;
    this.showModal = false ;
    this.modalBody='' ;
    this.modalTitle='' ;
    this.searchKey='' ;
    this.events = ['event1'] ;
    this.eventObservable = Observable.create((observer: any) => {
      // Runs on every search
      observer.next(this.searchKey);
    })
      .pipe(
        mergeMap((token: string) =>  this.bs.getSearchResults(token))
      );
  }


  ngOnInit() {
    this.getAppStatus() ;
    this.router.navigate(['fabric-overview'])
  }

  ngOnDestroy() {
    localStorage.removeItem('cul') ;
  }

  getAppStatus() {
    this.bs.getAppStatus().subscribe(
      (data)=>{
        this.getAppManagerStatus() ;
      } ,
      (error)=>{
        this.modalTitle='Error';
        this.modalBody='The app could not be started';
        this.showModal = true;
      }
    )
  }

  getAppManagerStatus() {
    this.bs.getAppManagerStatus().subscribe(
      (data)=>{
        if(data['manager']['status'] === 'stopped') {
          this.modalBody = 'Thread managers not running' ;
          this.modalTitle='Error';
          this.showModal = true ;
        }
        this.router.navigate(['/fabrics','fabric-overview']) ;
      },
      (error)=>{
        this.modalTitle='Error';
        this.modalBody='Could not reach thread manager'
        this.showModal = true;
      }
    )
  }

  onSearch(address) {
    if(address.length > 3) {
    this.bs.getSearchResults(address).subscribe(
      (data)=>{
        this.events = data['objects'] ;
        this.eventObservable = of()
      },
    (error)=>{

    }
    
  )
  
  }
}



  
  



  

}