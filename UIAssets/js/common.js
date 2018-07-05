
/* SHARED VARIABLES */
var vendorDomain = "Cisco"
var appId = "CSCvf18506"

/**
* listen for token objects from parent frame when running on apic
* success and error functions can be provided for proper action to take
*/
function appTokenRefresh(success, error){
    if(success === undefined){success = function(){}}
    if(error === undefined){error = function(e){}}
    window.addEventListener("message", function(e){
        try{
            var tokenObj =  JSON.parse(e.data);
            if(!tokenObj.hasOwnProperty("appId") || !tokenObj.hasOwnProperty("urlToken") ||
                !tokenObj.hasOwnProperty("token")){
                var err = {"statusText":"Token missing one or more required attributes: "}
                err.statusText+="appId, token, urlToken"
                error(err)
                return
            }
            Cookies.set("app_"+tokenObj.appId+"_token", tokenObj.token);            
            Cookies.set("app_"+tokenObj.appId+"_urlToken", tokenObj.urlToken);            
            console.log("setting token: "+ tokenObj.token+", urlToken: "+tokenObj.urlToken)
            success()
        } catch(e) {
            var err = {"statusText":"Cannot load token from backend"}
            console.log("error occurred: "+e)
            error(err)
        }
    });
}

/**
* check if app has started. If fail then recheck at pollAppTimeout interval.
* execute success/fail callbacks
*/
pollAppTimeout = 1000;
function pollAppStatus(success, fail){
    var url = "/api/aci/app-status/"
    if(success === undefined){success = function(){}}
    repeat = function(data, status_code, status_text){
        if(fail === undefined){}
        else{ fail(data,status_code,status_text)}
        //schedule repeat
        console.log((new Date())+" app has not yet started, recheck in "+pollAppTimeout+"ms");
        setTimeout(function(){ pollAppStatus(success,fail) }, pollAppTimeout);
    }
    json_get(url, function(data){
        console.log((new Date())+" app has started")
        return success()
    }, function(data,status_code,status_text){
        repeat(data,status_code,status_text)
    }) 
}

/**
* fadeVisible
* custom knockout binding using jqueries fadeIn/fadeOut
*/
ko.bindingHandlers.fadeVisible = {
    init: function(element, valueAccessor) {
        // Start visible/invisible according to initial value
        var shouldDisplay = valueAccessor();
        $(element).toggle(shouldDisplay);
    },
    update: function(element, valueAccessor) {
        // On update, fade in/out
        var shouldDisplay = valueAccessor();
        shouldDisplay ? $(element).fadeIn() : $(element).fadeOut();
    } 
};

/**
* loadingWhen
* custom knockout binding to display loader while isLoading flag is set
* (dependent on jquery)
* https://github.com/stevegreatrex/ko.plus
*/
ko.bindingHandlers.loadingWhen = {
    init: function (element) {
        // need to manually add the width as center calculation isn't picking it up correctly
        var html=   "<div class='koLoader text-center' style='width:68px'>" +
                        "<div class='text-small loading-dots loading-dots--info'>" +
                            "<span></span><span></span><span></span>" +
                        "</div>" +
                    "</div>"
        var 
            $element = $(element),
            currentPosition = $element.css("position")
            $loader = $(html).hide()

        //add the loader
        $element.append($loader);
        
        //make sure that we can absolutely position the loader against the 
        //original element
        if (currentPosition == "auto" || currentPosition == "static")
            $element.css("position", "relative");

        //center the loader
        $loader.css({
            position: "absolute",
            top: "50%",
            left: "50%",
            "margin-left": -($loader.width() / 2) + "px",
            "margin-top": -($loader.height() / 2) + "px"
        });
    },
    update: function (element, valueAccessor) {
        var isLoading = ko.utils.unwrapObservable(valueAccessor()),
            $element = $(element),
            //$childrenToHide = $element.children(":not(div.koLoader)"),
            $childrenToHide = $element.children(":not(div.koLoader)"),
            $loader = $element.find("div.koLoader");

        if (isLoading) {
            $childrenToHide.css("visibility", "hidden").attr(
                "disabled", "disabled");
            $loader.show();
        }
        else {
            //$loader.fadeOut("fast");
            $loader.hide()
            $childrenToHide.css("visibility", "visible").removeAttr("disabled");
        }
    }
};

//get url parameter
//http://www.jquerybyexample.net/2012/06/get-url-parameters-using-jquery.html
function get_url_param(sParam){
    var sPageURL = window.location.search.substring(1);
    var sURLVariables = sPageURL.split('&');
    for (var i = 0; i < sURLVariables.length; i++)
    {
        var sParameterName = sURLVariables[i].split('=');
        if (sParameterName[0] == sParam)
        {
            if(typeof(sParameterName[1])=="undefined"){return "";}
            return sParameterName[1];
        }
    }
    //parameter not found
    return "";
}

//define global timezoneOffset instead of calculating each call
var tzoffset = -(new Date()).getTimezoneOffset()
function timestamp_to_string(ts){
    //return moment(ts*1000).format('YYYY-MM-DD HH:mm:ss Z');
    return moment(ts*1000).format('YYYY-MM-DD HH:mm:ss');
}

//common ajax methods with support for proxy if app-mode cookies are set
function generic_ajax(url, method, data={},success=undefined,error=undefined){
    // default success/error functions if not provided
    if(success===undefined){ 
        success = function(data){console.log(data);}
    }
    if(error===undefined){
        error = function(json, status_code, status_text){
            console.log(json, status_text, status_code);
            if(json!==undefined && "error" in json){showAlertModal(json.error)}
            else{showAlertModal("An error occurred: ("+status_code+") "+status_text)}
        }
    }
    //appcenter_mode cookie set when app is loaded through app-start.html
    var app_set = Cookies.get("app_"+vendorDomain+"_"+appId+"_token")
    var headers = {}
    if(app_set!==undefined){
        data = {
            "url": url,
            "method": method,
            "data": data
        }
        method = "POST"
        url = "/appcenter/"+vendorDomain+"/"+appId+"/proxy.json"
        headers = {
            "DevCookie": Cookies.get("app_"+vendorDomain+"_"+appId+"_token"),
            "APIC-Challenge": Cookies.get("app_"+vendorDomain+"_"+appId+"_urlToken")
        }
    }
    return $.ajax({
        url:url, 
        type:method,
        data: Object.keys(data).length>0? ko.toJSON(data) : undefined,
        dataType: "json",
        contentType: "application/json; charset=utf-8",
        headers: headers,
        success: success,
        error: function (err){
            error(err.responseJSON, err.status, err.statusText)
        }       
    })
}

// short hand for each supported json method (GET/PUT/PATCH/POST/DELETE)
function json_get(url, success, error){
    return generic_ajax(url, "GET", data={}, success, error)
}
function json_put(url, data, success, error){
    return generic_ajax(url, "PUT", data, success, error)
}
function json_patch(url, data, success, error){
    return generic_ajax(url, "PATCH", data, success, error)
}
function json_post(url, data, success, error){
    return generic_ajax(url, "POST", data, success, error)
}
function json_delete(url, data, success, error){
    return generic_ajax(url, "DELETE", data, success, error)
}

// base object with common methods for most js objects
function baseModelObject(){
    var self=this
    self.__name__ = "baseModelObject"
    self._jsonify=[]    // list of jsonify-able attributes (defaults to all)
    self._subtypes={}   // list of observableArray object types

    // overwrite current object with provided object
    self.overwrite = function(obj){
        for(var key in obj){
            if(ko.isObservable(obj[key])){
                self[key](obj[key]());
            }
        }
    }

    // create ko.observables from JSON data
    self.fromJS = function(obj){
        for(var key in obj){
            //handle subtype oberservableArray check first
            //only if subtype is observableArray and subtype is baseModelObject 
            if((key in self._subtypes) && 
                self.hasOwnProperty(key) && ko.isObservable(self[key]) &&
                "push" in self[key] && (self._subtypes[key] instanceof Object)){
                var tobj = new self._subtypes[key]()
                if(tobj.__name__==self.__name__){
                    var data=[]
                    obj[key].forEach(function(sub_obj){
                        var new_obj = new self._subtypes[key]()
                        new_obj.fromJS(sub_obj)
                        data.push(new_obj)
                    })
                    self[key](data)
                }
            } else if(obj.hasOwnProperty(key) && self.hasOwnProperty(key)){
                if(ko.isObservable(self[key])){
                    self[key](obj[key])
                }else if(self[key] instanceof Object  &&
                    self[key].__name__==self.__name__){
                    self[key].fromJS(obj[key])
                }
            }
        }
    }

    // create json object of allowed ko objects (by default all ko objects)
    // also supporting recursive calls for toJS for sub baseModelObjects
    self.toJS = function(){
        var jsonify=[]
        if(self._jsonify.length>0){
            var jsonify = self._jsonify
        }else{  
            for(var key in self){jsonify.push(key);}
        }
        var obj = {}
        for(var i in jsonify){
            var key=jsonify[i];
            if(self.hasOwnProperty(key)){
                if(ko.isObservable(self[key]) && "push" in self[key]){
                    var data=[]
                    self[key]().forEach(function(sub_obj){
                        if(sub_obj instanceof Object && 
                            sub_obj.__name__==self.__name__){
                            data.push(sub_obj.toJS())
                        } else {
                            data.push(sub_obj)
                        }
                    })
                    obj[key] = data
                } else if(ko.isObservable(self[key])){
                    obj[key] = self[key]()
                } else if(self[key].__name__==self.__name__){
                    obj[key] = self[key].toJS()
                }
            }
        }
        return obj
    }
}

// modal functions
function hideModal(){
    $(".modal").addClass("hide")
    $(".modal-backdrop").addClass("hide")
}

function showAlertModal(msg){
    hideModal()
    $(".modal-alert").removeClass("hide")
    $(".modal-backdrop").removeClass("hide")
    $("#modal-alert-content").text(msg)
}

function showInfoModal(msg, html=false){
    hideModal()
    $(".modal-info").removeClass("hide")
    $(".modal-backdrop").removeClass("hide")
    if(html){
        $("#modal-info-content").html(msg)
    }else{
        $("#modal-info-content").html("")
        $("#modal-info-content").text(msg)
    }
}
function showModalForm(){
    hideModal()
    $(".modal-form").removeClass("hide")
    $(".modal-backdrop").removeClass("hide")
}

