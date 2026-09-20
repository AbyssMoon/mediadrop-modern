from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import db_dependency
from app.models import Group, User
from app.security import login_user, logout_user, validate_csrf, verify_legacy_password
from app.views import render

router = APIRouter()
Db = Depends(db_dependency)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login")
@router.post("/login/submit")
def login_submit(
    request: Request,
    csrf: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Db,
):
    validate_csrf(request, csrf)
    user = db.scalar(
        select(User)
        .where(or_(User.user_name == username, User.email_address == username))
        .options(selectinload(User.groups).selectinload(Group.permissions))
    )
    if user is None or not verify_legacy_password(password, user.password):
        return render(request, "login.html", error="error.invalid_credentials", status_code=401)
    login_user(request, user)
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    logout_user(request)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout_legacy(request: Request):
    logout_user(request)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
