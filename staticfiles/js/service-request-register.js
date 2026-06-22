document.getElementById(
  "registerForm"
).addEventListener(
  "submit",
  async function(e){

    e.preventDefault();

    const form = e.target;

    const services = Array.from(
      form.querySelectorAll(
        'input[name="services"]:checked'
      )
    ).map(item => item.value);

    const data = {
      name: form.name.value.trim(),
      whatsapp: form.whatsapp.value.trim(),
      password: form.password.value.trim(),
      seller_type: form.seller_type.value,
      city: form.city.value,
      district: form.district.value,
      address: form.address.value.trim(),
      map_link: form.map_link.value.trim(),
      services: services
    };

    const result =
      document.getElementById("result");

    result.className = 'msg';
    ZPTDom.setText(result, '');

    try {

      const response = await fetch(
        window.location.origin +
        "/api/service/create-service-seller/",
        {
          method:"POST",
          headers:{
            "Content-Type":"application/json"
          },
          body:JSON.stringify(data)
        }
      );

      const json = await response.json();

      if(!response.ok || json.error){

        result.className = 'msg error';
        ZPTDom.setText(result, json.error || "Ошибка регистрации");

        return;
      }

      result.className = 'msg success';
      ZPTDom.setText(result, "Исполнитель зарегистрирован");

      localStorage.setItem(
        "service_seller_id",
        json.seller_id
      );

      setTimeout(()=>{
        window.location.href =
          "/service-request/cabinet/";
      },700);

    } catch (error){

      result.className = 'msg error';
      ZPTDom.setText(result, "Ошибка соединения с сервером");
    }

});

const sellerTypeSelect =
  document.querySelector(
    'select[name="seller_type"]'
  );

const stoServices =
  document.getElementById('stoServices');

const detailingServices =
  document.getElementById(
    'detailingServices'
  );

sellerTypeSelect.addEventListener(
  'change',
  function(){

    if(this.value === 'detailing'){

      stoServices.style.display = 'none';

      detailingServices.style.display =
        'grid';

    }else{

      stoServices.style.display = 'grid';

      detailingServices.style.display =
        'none';
    }

});

const districts = {

  "Алматы":[
    "Алмалинский",
    "Ауэзовский",
    "Бостандыкский",
    "Жетысуский",
    "Медеуский",
    "Наурызбайский",
    "Турксибский"
  ],

  "Астана":[
    "Алматы",
    "Байконыр",
    "Есиль",
    "Нура",
    "Сарайшык"
  ]

};

const citySelect =
  document.querySelector(
    'select[name="city"]'
  );

const districtSelect =
  document.getElementById(
    'districtSelect'
  );

citySelect.addEventListener(
  'change',
  function(){

    const city = this.value;

    districtSelect.replaceChildren();

    if(!districts[city]){
      ZPTDom.fillSelectFromStrings(
        districtSelect,
        [],
        'Район не указан'
      );
      return;
    }

    ZPTDom.fillSelectFromStrings(
      districtSelect,
      districts[city],
      'Выберите район'
    );

});

function togglePassword(){

  const input =
    document.getElementById(
      'passwordInput'
    );

  input.type =
    input.type === 'password'
      ? 'text'
      : 'password';
}
