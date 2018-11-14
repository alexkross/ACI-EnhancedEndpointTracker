import { Injectable } from '@angular/core';
import { HttpInterceptor,HttpRequest,HttpHandler, HttpEvent,HttpResponse,HttpErrorResponse,HttpParams } from '@angular/common/http';
import {Observable, throwError} from 'rxjs' ;
import {catchError, map} from 'rxjs/operators';
import { environment } from '../../environments/environment';
import {CookieService} from 'ngx-cookie-service';

@Injectable({
  providedIn: 'root'
})
export class BackendInterceptorService implements HttpInterceptor {
  app_mode:boolean ;
  req:any;
  constructor(private cookieService: CookieService) { 
    this.app_mode = environment.app_mode ;
  }

  intercept(req: HttpRequest<any>, next: HttpHandler): Observable<HttpEvent<any>> {
    const initialBody = req.body || {};
      let params = new HttpParams();
      let body;
    if(this.app_mode) {
      body = {
        'url': '/api/' + req.urlWithParams,
        'method': req.method,
        'data': initialBody
      }
      req = req.clone({
        setHeaders: {
          'DevCookie': this.cookieService.get('app_' + environment.aci_vendor + '_' + environment.aci_appId + '_token'),
          'APIC-Challenge': this.cookieService.get('app_' + environment.aci_vendor + '_' + environment.aci_appId + '_urlToken')
        },
        body: body,
        url: environment.api_entry,
        params: params,
        method: 'post',
      });
    }else{
      req = req.clone({
        withCredentials: true
      });
    }
    
    return next.handle(req).pipe(map(resp => {
      if (resp instanceof HttpResponse) {
        return resp;
      }
    }), catchError(err => {
      if (err instanceof HttpErrorResponse && err.status === 401 && localStorage.getItem('isLoggedIn') === 'true') {
        
      }
      return throwError(err);
    }),);
  }
}