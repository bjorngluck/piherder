from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select
from ..database import get_session
from ..models import User
from ..security.auth import (
    authenticate_user, create_access_token, get_password_hash, get_current_user
)
from .. import templates as templates_mod

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"title": "Login"}
    )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    user = authenticate_user(session, email, password)
    if not user:
        # Simple error handling - in real app we'd use flash or template error
        return RedirectResponse("/auth/login?error=invalid", status_code=303)

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/", status_code=303)
    # Store raw token (middleware/dependency adds Bearer when needed)
    response.set_cookie("access_token", token, httponly=True, max_age=60*60*24*7)
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"title": "Register"}
    )


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    # Very simple first-user registration (no rate limit yet)
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"title": "Register", "error": "User with that email already exists"}
        )
    try:
        # Truncate is also done in get_password_hash, but warn user
        hashed = get_password_hash(password)
        user = User(email=email, hashed_password=hashed)
        session.add(user)
        session.commit()
        session.refresh(user)
        return RedirectResponse("/auth/login", status_code=303)
    except Exception as e:
        # Catch DB or other errors, show friendly message
        msg = "Registration failed. Please try a different email or shorter password."
        return templates_mod.templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"title": "Register", "error": msg}
        )


@router.get("/logout")
async def logout():
    """Clear auth cookie and return to login. (GET is acceptable for logout in this app.)"""
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie("access_token")
    return response


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, user: User = Depends(get_current_user)):
    """Stub account page for future profile, avatar, and password management."""
    return templates_mod.templates.TemplateResponse(
        request=request,
        name="account.html",
        context={"title": "Account", "user": user}
    )
