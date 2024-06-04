from fastapi import FastAPI,BackgroundTasks, APIRouter, Depends, HTTPException, status, File, UploadFile, Request, Form, Response
from fastapi.responses import FileResponse
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy.orm import Session
from database import AsyncSessionLocal
import uuid


from app.Utils.chatgpt import get_last_message
from app.Utils.regular_send import send
from app.Utils.Auth import get_current_user
from app.Utils.regular_update import job, update_notification, update_database
from app.Utils.regular_send import send_opt_in_phone
from app.Utils.sendgrid import send_opt_in_email
import app.Utils.database_handler as crud
from app.Model.Settings import SettingsModel
from app.Model.MainTable import MainTableModel
from app.Model.ScrapingStatusModel import ScrapingStatusModel
from app.Model.LastMessageModel import LastMessageModel
from pydantic import EmailStr

from copy import deepcopy
from typing import Annotated
from datetime import datetime
import os
import json


from dotenv import load_dotenv


load_dotenv()
router = APIRouter()

# Dependency to get the database session
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

@router.get('/update-db')
def update_db(source: str, email: Annotated[str, Depends(get_current_user)], db: Session = Depends(get_db)):
    print("dashboard - source: ", source)
    status = crud.get_status(db)
    update_status = {}
    if source == "BuilderTrend":
        update_status = ScrapingStatusModel(buildertrend_total=status.buildertrend_total, buildertrend_current=0, xactanalysis_total=status.xactanalysis_total, xactanalysis_current=status.xactanalysis_current).dict()
    else:
        update_status = ScrapingStatusModel(buildertrend_total=status.buildertrend_total, buildertrend_current=status.buildertrend_current, xactanalysis_total=status.xactanalysis_total, xactanalysis_current=0).dict()
    print("**update_status: ", update_status)
    crud.update_status(db, status.id, **update_status)
    
    job(source)
    return True

@router.post('/get-scraped-result')
async def scraped_result(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    data = await request.json()
    print("dashboard - data: ", data)
    background_tasks.add_task(update_database, data)
    # Process the raw JSON data here
    return {"received": len(data), "message": "Raw data processed successfully"}

@router.get('/table')
async def get_table(email: Annotated[str, Depends(get_current_user)], db: Session = Depends(get_db)):
    main_table_data = crud.get_main_table(db)  # Replace with appropriate CRUD operation
    result = []
    for item in main_table_data:
        temp_result = []
        new_item = item._asdict()
        if new_item['message_status'] != 3:
            temp_result.append(new_item)
        
        history_messages = crud.get_message_history_by_project_id_as_list(db, item.project_id)
        if history_messages:
            print("new_item1: ", new_item)
        for history_message in history_messages:
            tmp_item = deepcopy(new_item)
            tmp_item['last_message'] = history_message.message
            tmp_item['message_status'] = 3
            tmp_item['sent_timestamp'] = history_message.sent_time
            tmp_item['project_id'] = uuid.uuid4().int
            temp_result.append(tmp_item)
            print("tmp_item: ", tmp_item)
        temp_result.reverse()
        print("len: ", len(temp_result))
        result.extend(temp_result)
            
    return result

@router.get('/qued')
async def make_qued(email: Annotated[str, Depends(get_current_user)], project_id: int, db: Session = Depends(get_db)):
    qued_time = datetime.utcnow()
    # print("qued_time", qued_time)
    crud.update_project(db, project_id, message_status=2, qued_timestamp=qued_time)
    return {"success": "true"}

@router.get('/cancel-qued')
async def cancel_qued(email: Annotated[str, Depends(get_current_user)], project_id: int, db: Session = Depends(get_db)):
    crud.update_project(db, project_id, message_status=1, qued_timestamp=None)
    return {"success": "true"}

@router.get('/set-sent')
async def set_sent(email: Annotated[str, Depends(get_current_user)], project_id: int, db: Session = Depends(get_db)):
    sent_time = datetime.utcnow()
    print("sent_time", sent_time)
    crud.update_project(db, project_id, message_status=3)
    ret = send(project_id, db)  # Replace with appropriate send operation
    if ret:
        return {"success": "true"}
    else:
        return {"success": "false"}

@router.get('/change-status')
async def change_status(email: Annotated[str, Depends(get_current_user)], customer_id: int, method: int, db: Session = Depends(get_db)):
    crud.update_sending_method(db, customer_id, method=method)
    return {"success": "true"}

@router.post('/update-last-message')
async def update_last_message(email: Annotated[str, Depends(get_current_user)], last_message: LastMessageModel, db: Session = Depends(get_db)):
    print("message: ", last_message.message)
    crud.update_project(db, last_message.project_id, last_message=last_message.message)
    return {"success": "true"}

@router.get('/download-project-message')
async def download_project_message(email: Annotated[str, Depends(get_current_user)], project_id: int, db: Session = Depends(get_db)):
    message = crud.get_message_history_by_project_id(db, project_id)
    print(message)
    
    # Write data to a text file
    file_path = 'message.txt'
    with open(file_path, 'w') as f:
        f.write(message + '\n')
    
    # Ensure file was saved
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Return file as response
    return FileResponse(file_path, media_type='application/octet-stream', filename='message.txt')



@router.get('/download-customer-message')
async def download_customer_message(email: Annotated[str, Depends(get_current_user)], customer_id: int, db: Session = Depends(get_db)):
    message = crud.get_message_history_by_customer_id(db, customer_id)
    
    # Write data to a text file
    file_path = 'customer_message.txt'
    with open(file_path, 'w') as f:
        f.write(message + '\n')
    
    # Ensure file was saved
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Return file as response
    return FileResponse(file_path, media_type='application/octet-stream', filename='customer_message.txt')

@router.get('/delete-customer')
async def delete_customer_route(email: Annotated[str, Depends(get_current_user)], customer_id: int, db: Session = Depends(get_db)):
    crud.delete_customer(db, customer_id)
    return {"success": "true"}

@router.get('/send')
async def send_message_route(email: Annotated[str, Depends(get_current_user)], db: Session = Depends(get_db)):
    send()
    return {"success": "true"}

@router.post('/set-variables')
async def set_variables_route(variables: SettingsModel, db: Session = Depends(get_db)):
    
    data = crud.get_variables(db)
    print(data)
    print("dashboard - set_variables", variables)
    if data is None:
        crud.create_variables(db, **variables.dict())
    else:
        update_data = {k: (getattr(data, k) if v == "" else v) for k, v in variables.dict().items()}
        crud.update_variables(db, data.id, **update_data)
    return {"success": "true"}

@router.get('/timer')
async def get_timer(email: Annotated[str, Depends(get_current_user)], db: Session = Depends(get_db)):
    data = crud.get_variables(db)
    if data is None:
        return None
    else:
        print(data.timer)
        return data.timer
        
        
    return {"success": "true"}

@router.get('/rerun-chatgpt')
async def rerun_chatgpt_route(background_tasks: BackgroundTasks, email: Annotated[str, Depends(get_current_user)], db: Session = Depends(get_db)):
    background_tasks.add_task(update_notification, db)
    return {"success": "true"}



@router.get('/set-opt-in-status-email')
def set_opt_in_status_email(email: Annotated[str, Depends(get_current_user)], customer_id: int, opt_in_status_email: int, db: Session = Depends(get_db)):
    print("dashboard - customer_id: ", customer_id)
    customer = crud.get_customer(db, customer_id)
    if opt_in_status_email == 1:
        send_opt_in_email(customer_id, customer.email, db)
    crud.update_opt_in_status_email(db, customer_id, opt_in_status_email)
    return True

@router.get('/set-opt-in-status-phone')
def set_opt_in_status_phone(email: Annotated[str, Depends(get_current_user)], customer_id: int, opt_in_status_phone: int, db: Session = Depends(get_db)):
    print("dashboard - customer_id: ", customer_id)
    customer = crud.get_customer(db, customer_id)
    if opt_in_status_phone == 1:
        send_opt_in_phone(customer.phone, db)
    crud.update_opt_in_status_phone(db, customer_id, opt_in_status_phone)
    return True

@router.get('/confirm-opt-in-status')
def set_opt_in_status(customer_id: int, response: str, db: Session = Depends(get_db)):
    print("dashboard - confirm-opt-in-status - customer_id: ", customer_id)
    
    crud.update_opt_in_status_email(db, customer_id, 2 if response == "accept" else 3)
    
    data = crud.get_status(db)
    if data is not None:
        crud.set_db_update_status(db, data.id, 1)
    
    if response == "accept":
        return "Sent Successfully! Congulatulations!"
    else:
        return "Sent Successfully!"
    

@router.post("/incoming-sms")
async def handle_sms(Body: str = Form(...), From: str = Form(...), db: Session = Depends(get_db)):
    # Convert message to uppercase for consistent matching
    incoming_msg = Body.strip().upper()
    From = From.replace(' ', '')
    print("dashboard - From: ", From)
    # Create a Twilio MessagingResponse object
    response = MessagingResponse()
    
    # Check if the incoming message is a recognized keyword
    if incoming_msg == "#STOP" or incoming_msg == "STOP":
        print("dashboard - incoming_msg:", incoming_msg)
        customer = crud.find_customer_with_phone(db, From)
        if customer is not None:
            crud.update_opt_in_status_phone(db, customer.id, 3) # Set as Opt Out
        response.message("You have been unsubscribed from messages. Reply with #START to subscribe again.")
        
    elif incoming_msg == "#START" or incoming_msg == "START" :
        print("dashboard - incoming_msg:", incoming_msg)
        # Update your database to mark this number as opted-in
        customer = crud.find_customer_with_phone(db, From)
        print("dashboard - From:", From)
        print("dashboard - customer:", customer.id)
        if customer is not None:
            crud.update_opt_in_status_phone(db, customer.id, 2) # Set as Opt In
        response.message("You have been subscribed to messages.")
    else:
        print("dashboard - incoming_msg:", incoming_msg)
        # The message is not a recognized keyword
        response.message("Sorry, we did not understand your message. Reply with #STOP to unsubscribe or #START to subscribe.")

    data = crud.get_status(db)
    if data is not None:
        crud.set_db_update_status(db, data.id, 1)

    # Return the response to Twilio
    print("dashboard - response: ", response)
    return Response(content=str(response), media_type="application/xml")


@router.get('/variables')
def get_variables(email: Annotated[str, Depends(get_current_user)], db: Session = Depends(get_db)):
    
    data = crud.get_variables(db)
    return data

@router.get('/check-database-update')
async def get_variables(email: Annotated[str, Depends(get_current_user)], db: Session = Depends(get_db)):
    
    data = crud.get_status(db)
    if data is None:
        return False
    
    db_update_status = data.db_update_status
    print("dashboard - data.db_update_status: ", data.db_update_status)
    
    if db_update_status:
        crud.set_db_update_status(db, data.id, 0)
        return True
    else:
        return False


@router.post("/update-scraping-status")
async def update_scraping_status(scraping_status: ScrapingStatusModel, db: Session = Depends(get_db)):
    status = crud.get_status(db)
    print("scraping_status: ", scraping_status)
    if status is not None:
        update_status = {k: (getattr(status, k) if v == -1 else v) for k, v in scraping_status.dict().items()}
        crud.update_status(db, status.id, **update_status)
    return {"success": "true"}


@router.get("/check-scraping-status")
async def check_scraping_status(db: Session = Depends(get_db)):
    status = crud.get_status(db)
    return status