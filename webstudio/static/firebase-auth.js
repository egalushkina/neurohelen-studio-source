import {initializeApp} from 'https://www.gstatic.com/firebasejs/12.4.0/firebase-app.js';
import {getAuth,GoogleAuthProvider,signInWithPopup,signInWithEmailAndPassword,createUserWithEmailAndPassword,sendEmailVerification,sendPasswordResetEmail,signOut,setPersistence,inMemoryPersistence} from 'https://www.gstatic.com/firebasejs/12.4.0/firebase-auth.js';
export async function connect(config,language){
 const auth=getAuth(initializeApp(config));auth.languageCode=language;await setPersistence(auth,inMemoryPersistence);
 async function finish(result){if(!result.user.emailVerified){await sendEmailVerification(result.user);await signOut(auth);throw Error('Подтвердите email по ссылке в письме, затем войдите.')}return result.user.getIdToken(true)}
 return {
  google:async()=>finish(await signInWithPopup(auth,new GoogleAuthProvider())),
  password:async(email,password,register)=>finish(await (register?createUserWithEmailAndPassword:signInWithEmailAndPassword)(auth,email,password)),
  reset:email=>sendPasswordResetEmail(auth,email),
  logout:()=>signOut(auth)
 };
}
